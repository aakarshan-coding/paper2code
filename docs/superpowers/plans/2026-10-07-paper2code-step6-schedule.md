# paper2code Step 6: Daily Run, Runs Repository, Dashboard and Schedule — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the loop run unattended once a day: one command creates today's run, runs the seven stages, pushes the run directory to the runs repository after every stage, rebuilds a static dashboard over every run, and sends an optional notification; a Modal scheduled function runs that command daily with secrets from Modal, and a preflight check refuses to spend money when something is not set up.

**Architecture:** Four small pieces around the existing pipeline. `manager/runs_repo.py` wraps git for the runs repository (clone or init, commit, push with a token that is never logged). `dashboard/build.py` is a pure-Python static site generator that reads every `run.json`, `verdict.json` and `summary.md` under the runs root and writes `index.html` plus one page per run into `<runs_root>/docs/`. `manager/daily.py` is the orchestration: preflight, create the run (numeric suffix for a second run on the same day), run the pipeline with a stage hook that publishes after every stage, rebuild the dashboard, publish, notify. `sandbox/modal_app.py` gains `daily_run`, a scheduled function whose image installs the package (the Agent SDK's Linux wheel bundles the `claude` CLI, verified) and whose secrets come from one Modal secret. Nothing about the stages changes.

**Tech Stack:** `git` via `subprocess` (`run_killable` style, no shell), `html.escape`, `httpx` (already a dependency) for the notification POST, `modal.Cron`, `modal.Secret.from_name`, `modal.Image.pip_install_from_pyproject` + `add_local_python_source` + `add_local_file` for `config.yaml`.

**Spec:** `docs/superpowers/specs/2026-09-30-paper2code-design.md` (sections 4, 5, 11, 12, 13, 14, 16)

**Facts established before planning (2026-10-07):**
- `claude-agent-sdk 0.2.164` ships platform wheels; the `manylinux_2_17_x86_64` wheel contains `_bundled/claude` (251 MB) and the SDK's transport looks there first, so a Linux image that pip-installs the SDK can start the builder with no Node and no PATH entry. The Windows wheel bundles `claude.exe` the same way.
- `modal.App.function` accepts `schedule=modal.Cron("0 13 * * *", timezone="UTC")`, `secrets=[modal.Secret.from_name(name, required_keys=[...])]`, `timeout` (seconds; the manager needs hours), `cpu`, `memory`. A function can create sandboxes and call `run_tests_remote` while it runs.
- No `gh` CLI on this machine; `git` pushes use Git Credential Manager (`credential.helper=manager`). No `GITHUB_TOKEN` in the environment. The runs repository does not exist yet; creating it and a push token are the author's actions (only they can), so the plan treats the remote URL and the token as configuration that may be empty: with no URL the runs root is a plain directory, everything else still works.
- `.gitignore` already excludes `runs/`.

## Global Constraints

- Spec 4: `runs/YYYY-MM-DD/`, a second run the same day gets a numeric suffix (`2026-10-07-2`); `runs/` is its own git repository; the manager commits and pushes after every stage.
- Spec 5: `seen.jsonl` lives at the root of `runs/` (already true; it therefore lives in the runs repository and survives across machines).
- Spec 11: `summary.md` already exists; commit and push the run directory; regenerate the dashboard, a static site listing every run with outcome and cost plus per-run drill-down; notifications off by default.
- Spec 12: one scheduled Modal function running the manager; secrets `CLAUDE_CODE_OAUTH_TOKEN`, `OPENAI_API_KEY`, a GitHub token; `ANTHROPIC_API_KEY` deliberately absent.
- Money: `daily` spends scout, scoper and inspector tokens, GPU seconds and subscription time; it must refuse to start when preflight fails (so a forgotten deploy or a missing key costs nothing), and the unit suite never calls a model, Modal, git remotes on the network, or a notification URL.
- Secrets never reach a log, an exception message, `run.json`, the dashboard or git history. The push URL with the token is built only for the push command's argument list.
- Resume safety: `daily` on an existing run directory resumes it (the pipeline already does); publishing twice is idempotent (nothing to commit → no commit, push of an up-to-date branch is fine).
- Commit after every task with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; tests with `TEMP`/`TMP`/`TMPDIR` on the scratchpad; big edits via a Python patch script written with the Write tool.

## Review Focus

1. **A run that ended early** (error in `fetch`, `no_candidates`, a crash before any verdict) must still render on the dashboard with its outcome and whatever files exist, not break the whole site. Pinned to Task 2 (`test_dashboard_renders_runs_without_verdict_or_summary`).
2. **A paper title or summary containing HTML** (`<script>`, `&`, quotes) must appear as text on the dashboard, never as markup. Pinned to Task 2 (`test_dashboard_escapes_untrusted_text`).
3. **Publishing fails** (no network, token rejected, remote missing): the run record on disk is complete and unchanged, the daily command reports the failure, and the next publish retries everything not yet pushed. Pinned to Task 1 (`test_publish_failure_leaves_the_repo_committed_and_reports`) and Task 3 (`test_daily_finishes_the_run_when_publish_fails`).
4. **Two runs on the same day** (a manual rerun after a failure) must get `-2`, `-3` suffixes, never overwrite or resume the earlier directory by accident. Pinned to Task 3 (`test_daily_second_run_same_day_gets_a_suffix`).
5. **A missing prerequisite** (no OpenAI key with `--llm openai`, no deployed GPU function without `--no-gpu`, no `claude` CLI with `--builder agent`) must stop `daily` before any run directory is created or any model is called. Pinned to Task 3 (`test_daily_refuses_when_preflight_fails`).

---

### Task 1: The runs repository

**Files:**
- Create: `src/paper2code/manager/runs_repo.py`
- Modify: `src/paper2code/config.py`, `config.yaml`
- Test: `tests/test_runs_repo.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `subprocess`, `Path`.
- Produces:
  - `class GitError(Exception)`.
  - `class RunsRepo(root: Path, remote_url: str = "", token_env: str = "GITHUB_TOKEN", git: str = "git")`:
    - `ensure() -> None`: if `root/.git` exists, nothing; elif `remote_url` and the root does not exist or is empty, `git clone --depth 1 <authed_url> root` (falls back to `git init` + `git remote add origin <url>` when the clone fails because the remote is empty, i.e. the error text contains `empty repository` or `couldn't find remote ref`); else `git init` (and `git remote add origin` when a URL is given). Sets `user.name`/`user.email` to `paper2code`/`paper2code@localhost` locally if unset. Ensures the branch is `main`.
    - `commit(paths: list[Path], message: str) -> bool`: `git add -A -- <paths relative to root>` then commit; returns False when there was nothing to commit.
    - `push() -> None`: no-op when `remote_url` is empty; otherwise `git push -u <authed_url> HEAD:main`; raises `GitError(stderr with the token redacted)` on failure.
    - `publish(paths, message) -> bool`: `commit` then `push`; returns whether a commit was made. A push failure raises `GitError` after the commit stays local.
    - `_authed_url() -> str`: for `https://` URLs with a token in `os.environ[token_env]`, `https://x-access-token:<token>@host/path`; otherwise the URL unchanged. `_redact(text) -> str` replaces the token with `***`.
  - Config: `runs_repo_url: str = ""` and the loader line. `config.yaml`: `runs_repo_url: ""   # e.g. https://github.com/<you>/paper2code-runs ; empty = local directory only, no push`.

- [ ] **Step 1: Write the failing tests**

`tests/test_runs_repo.py`:

```python
"""RunsRepo against local bare repositories; no network."""
import subprocess
from pathlib import Path

import pytest

from paper2code.manager.runs_repo import GitError, RunsRepo


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL).stdout


def _bare(tmp_path) -> Path:
    bare = tmp_path / "remote.git"
    _git("init", "--bare", "--initial-branch=main", str(bare), cwd=tmp_path)
    return bare


def test_ensure_inits_a_plain_directory_without_a_remote(tmp_path):
    root = tmp_path / "runs"
    repo = RunsRepo(root)
    repo.ensure()
    assert (root / ".git").exists()
    (root / "seen.jsonl").write_text("{}\n", encoding="utf-8")
    assert repo.publish([root / "seen.jsonl"], "seed") is True
    assert repo.publish([root / "seen.jsonl"], "again") is False  # nothing to commit
    assert "seed" in _git("log", "--oneline", cwd=root)


def test_ensure_clones_or_inits_against_an_empty_remote_and_pushes(tmp_path):
    bare = _bare(tmp_path)
    root = tmp_path / "runs"
    repo = RunsRepo(root, remote_url=str(bare))
    repo.ensure()
    (root / "2026-10-07").mkdir()
    (root / "2026-10-07" / "run.json").write_text("{}", encoding="utf-8")
    assert repo.publish([root / "2026-10-07"], "run 2026-10-07: fetch") is True
    assert "run 2026-10-07: fetch" in _git("log", "--oneline", "main", cwd=bare)
    # a second checkout of the remote sees the run
    other = tmp_path / "other"
    RunsRepo(other, remote_url=str(bare)).ensure()
    assert (other / "2026-10-07" / "run.json").exists()


def test_publish_failure_leaves_the_repo_committed_and_reports(tmp_path):
    root = tmp_path / "runs"
    repo = RunsRepo(root, remote_url=str(tmp_path / "does-not-exist.git"))
    repo.ensure()
    (root / "a.txt").write_text("a", encoding="utf-8")
    with pytest.raises(GitError):
        repo.publish([root / "a.txt"], "first")
    assert "first" in _git("log", "--oneline", cwd=root)  # committed locally
    assert _git("status", "--porcelain", cwd=root) == ""
    # once the remote exists the next publish pushes everything
    _git("init", "--bare", "--initial-branch=main", str(tmp_path / "does-not-exist.git"), cwd=tmp_path)
    (root / "b.txt").write_text("b", encoding="utf-8")
    repo.publish([root / "b.txt"], "second")
    assert "first" in _git("log", "--oneline", "main", cwd=tmp_path / "does-not-exist.git")


def test_token_is_used_for_https_and_never_appears_in_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret123")
    repo = RunsRepo(tmp_path / "runs", remote_url="https://github.com/x/y")
    assert repo._authed_url() == "https://x-access-token:ghp_secret123@github.com/x/y"
    assert "ghp_secret123" not in repo._redact("fatal: https://x-access-token:ghp_secret123@github.com/x/y rejected")
    monkeypatch.delenv("GITHUB_TOKEN")
    assert repo._authed_url() == "https://github.com/x/y"
    local = RunsRepo(tmp_path / "runs2", remote_url=str(tmp_path / "r.git"))
    assert local._authed_url() == str(tmp_path / "r.git")
```

Append to `tests/test_config.py`:

```python
def test_step6_config_defaults():
    cfg = Config()
    assert cfg.runs_repo_url == "" and cfg.notify_url == "" and cfg.daily_builder == "agent"
    assert cfg.schedule_cron == "0 13 * * *" and cfg.test_function_timeout_s == 1800
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_runs_repo.py tests/test_config.py -q`
Expected: `ModuleNotFoundError: No module named 'paper2code.manager.runs_repo'`; config test fails on the missing attribute.

- [ ] **Step 3: Implement**

`src/paper2code/manager/runs_repo.py`:

```python
"""The runs repository: `runs/` is its own git repository (spec 4). The manager commits and pushes the
run directory after every stage so partial runs are visible remotely. The push token is read from the
environment, put only into the push command's URL, and redacted from any error text."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


class GitError(Exception):
    pass


class RunsRepo:
    def __init__(self, root: Path, remote_url: str = "", token_env: str = "GITHUB_TOKEN", git: str = "git") -> None:
        self.root = Path(root)
        self.remote_url = remote_url
        self.token_env = token_env
        self.git = git

    # -- helpers -------------------------------------------------------------------------------
    def _token(self) -> str:
        return os.environ.get(self.token_env, "")

    def _authed_url(self) -> str:
        token = self._token()
        if token and self.remote_url.startswith("https://"):
            return "https://x-access-token:" + token + "@" + self.remote_url[len("https://"):]
        return self.remote_url

    def _redact(self, text: str) -> str:
        token = self._token()
        return text.replace(token, "***") if token else text

    def _run(self, *args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            [self.git, *args], cwd=str(cwd or self.root), capture_output=True, text=True,
            stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
        )
        if check and proc.returncode != 0:
            raise GitError(self._redact(f"git {' '.join(args[:2])} failed: {proc.stderr.strip()[:500]}"))
        return proc

    def _configure(self) -> None:
        if self._run("config", "user.email", check=False).returncode != 0:
            self._run("config", "user.email", "paper2code@localhost")
            self._run("config", "user.name", "paper2code")
        self._run("symbolic-ref", "HEAD", "refs/heads/main", check=False)

    # -- public --------------------------------------------------------------------------------
    def ensure(self) -> None:
        if (self.root / ".git").exists():
            self._configure()
            return
        empty = not self.root.exists() or not any(self.root.iterdir())
        if self.remote_url and empty:
            self.root.parent.mkdir(parents=True, exist_ok=True)
            proc = self._run("clone", "--depth", "1", "--branch", "main", self._authed_url(), str(self.root), cwd=self.root.parent, check=False)
            if proc.returncode == 0:
                self._run("remote", "set-url", "origin", self.remote_url)  # never leave the token in .git/config
                self._configure()
                return
            text = proc.stderr.lower()
            if "empty repository" not in text and "couldn't find remote ref" not in text and "remote branch main not found" not in text:
                raise GitError(self._redact(f"git clone failed: {proc.stderr.strip()[:500]}"))
        self.root.mkdir(parents=True, exist_ok=True)
        self._run("init", "--initial-branch=main")
        if self.remote_url:
            self._run("remote", "add", "origin", self.remote_url)
        self._configure()

    def commit(self, paths: list[Path], message: str) -> bool:
        rel = [str(Path(p).resolve().relative_to(self.root.resolve())) for p in paths]
        self._run("add", "-A", "--", *rel)
        if self._run("diff", "--cached", "--quiet", check=False).returncode == 0:
            return False
        self._run("commit", "-q", "-m", message)
        return True

    def push(self) -> None:
        if not self.remote_url:
            return
        self._run("push", "-q", "-u", self._authed_url(), "HEAD:main")

    def publish(self, paths: list[Path], message: str) -> bool:
        made = self.commit(paths, message)
        self.push()
        return made
```

(`git clone --depth 1` of an empty remote fails with "warning: You appear to have cloned an empty repository" on some versions and succeeds on others; both paths end in a usable repo with `origin` set. The test for the empty remote covers whichever happens locally; the ledger records which.)

Config additions in `Config` and `load_config` (same pattern as before):

```python
    runs_repo_url: str = ""
    notify_url: str = ""
    daily_builder: str = "agent"
    schedule_cron: str = "0 13 * * *"
    test_function_timeout_s: int = 1800
```

`config.yaml`:

```yaml
runs_repo_url: ""              # e.g. https://github.com/<you>/paper2code-runs ; empty = local directory, no push
notify_url: ""                 # POST a JSON summary here when a daily run finishes; empty = off
daily_builder: agent           # what `paper2code daily` uses
schedule_cron: "0 13 * * *"    # UTC; Modal cron for daily_run (deploy after changing)
test_function_timeout_s: 1800  # must match PAPER2CODE_TEST_FUNCTION_TIMEOUT at deploy; must exceed run_tests_timeout_s
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_runs_repo.py tests/test_config.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/paper2code/manager/runs_repo.py src/paper2code/config.py config.yaml tests/test_runs_repo.py tests/test_config.py
git commit -m "Runs repository: clone or init, commit after a stage, push with a redacted token

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: The dashboard

**Files:**
- Create: `src/paper2code/dashboard/__init__.py`, `src/paper2code/dashboard/build.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `RunRecord.load`, `Verdict.load`, `summary.md`, `build.log` rows, `seen.jsonl` size.
- Produces:
  - `collect_runs(runs_root: Path) -> list[RunRow]` where `@dataclass RunRow(run_id, run_dir, outcome: str, stage: str, paper_title, paper_url, arxiv_id, spent_usd, gpu_seconds, test_runs_used, flags: int, confidence: float | None, started_at, finished_at, error: str)`; one row per directory containing `run.json`, sorted newest first; directories whose `run.json` is unreadable become a row with `outcome="unreadable"`.
  - `build_site(runs_root: Path, out_dir: Path | None = None) -> Path`: writes `out_dir/index.html` (default `runs_root/docs`), `out_dir/runs/<run_id>/index.html` per run, and copies the run's text files (`run.json`, `summary.md`, `verdict.json`, `build.log`, `candidates.jsonl`, `selected.json`, `scope/spec.md`, `scope/interface.md`, `scope/tests/public/*.py`, `workspace/**/*.py`) next to the page as `.txt` files for drill-down. Hidden tests are NOT copied (the dashboard may be public). Returns `out_dir`.
  - All text through `html.escape`. One inline stylesheet, no JavaScript, no external resources.
  - The index: a header with counts per outcome and total spend; a table with date, paper (link), outcome (as a badge class), stage, spend, GPU seconds, test runs, flags, inspector confidence.

- [ ] **Step 1: Write the failing tests**

`tests/test_dashboard.py`:

```python
import json
import shutil
from datetime import date

from paper2code.dashboard.build import build_site, collect_runs
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Caps, Paper, RunError, create_run
from paper2code.manager.verdict import Flag, Verdict


def _completed(tmp_path, canary_dir, day, title="EMA denoising canary"):
    rec = create_run(tmp_path, day, Caps(), 10.0)
    rec.paper = Paper("canary-0001", title, "https://example.invalid/canary")
    rec.stage = "report"
    rec.outcome = Outcome.COMPLETED_SUSPICIOUS
    rec.budget.spent_usd = 1.5
    rec.budget.gpu_seconds = 7.0
    rec.counters.test_runs_used = 2
    rec.save()
    shutil.copytree(canary_dir / "scope", rec.run_dir / "scope")
    shutil.copytree(canary_dir / "reference", rec.run_dir / "workspace")
    Verdict(outcome=Outcome.COMPLETED_SUSPICIOUS, hidden_passed=["h::a"], flags=[Flag("hardcoded_result", "canary_method.py", 18, "<b>note</b>")],
            summary="looks fine & dandy", confidence=0.9).write(rec.run_dir)
    (rec.run_dir / "summary.md").write_text(f"# Run {rec.run_id}\n\n- **Paper:** {title}\n", encoding="utf-8")
    (rec.run_dir / "build.log").write_text(json.dumps({"ts": "t", "event": "session_start"}) + "\n", encoding="utf-8")
    return rec


def test_collect_runs_sorts_newest_first_and_reads_fields(tmp_path, canary_dir):
    _completed(tmp_path, canary_dir, date(2026, 10, 6))
    b = _completed(tmp_path, canary_dir, date(2026, 10, 7))
    rows = collect_runs(tmp_path)
    assert [r.run_id for r in rows] == [b.run_id, "2026-10-06"]
    assert rows[0].outcome == "completed_suspicious" and rows[0].spent_usd == 1.5 and rows[0].flags == 1 and rows[0].confidence == 0.9


def test_dashboard_renders_runs_without_verdict_or_summary(tmp_path, canary_dir):
    rec = create_run(tmp_path, date(2026, 10, 5), Caps(), 10.0)
    rec.stage = "fetch"
    rec.outcome = Outcome.ERROR
    rec.error = RunError("fetch", "exception", "ConnectionError: arxiv down")
    rec.save()
    (tmp_path / "2026-10-04").mkdir()
    (tmp_path / "2026-10-04" / "run.json").write_text("{not json", encoding="utf-8")
    _completed(tmp_path, canary_dir, date(2026, 10, 7))
    out = build_site(tmp_path)
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "2026-10-05" in index and "error" in index and "arxiv down" in index
    assert "2026-10-04" in index and "unreadable" in index
    page = (out / "runs" / "2026-10-05" / "index.html").read_text(encoding="utf-8")
    assert "no verdict" in page.lower() and "ConnectionError" in page


def test_dashboard_escapes_untrusted_text(tmp_path, canary_dir):
    rec = _completed(tmp_path, canary_dir, date(2026, 10, 7), title='<script>alert(1)</script> & "quotes"')
    out = build_site(tmp_path)
    index = (out / "index.html").read_text(encoding="utf-8")
    page = (out / "runs" / rec.run_id / "index.html").read_text(encoding="utf-8")
    for text in (index, page):
        assert "<script>alert(1)</script>" not in text and "&lt;script&gt;" in text
    assert "<b>note</b>" not in page and "&lt;b&gt;note&lt;/b&gt;" in page
    assert "&amp; dandy" in page


def test_dashboard_copies_drilldown_files_but_never_hidden_tests(tmp_path, canary_dir):
    rec = _completed(tmp_path, canary_dir, date(2026, 10, 7))
    out = build_site(tmp_path)
    run_out = out / "runs" / rec.run_id
    assert (run_out / "summary.md.txt").exists() and (run_out / "verdict.json.txt").exists()
    assert (run_out / "scope" / "spec.md.txt").exists() and (run_out / "scope" / "tests" / "public" / "test_claim.py.txt").exists()
    assert (run_out / "workspace" / "canary_method.py.txt").exists()
    assert not any("hidden" in p.as_posix() for p in run_out.rglob("*"))
    page = (run_out / "index.html").read_text(encoding="utf-8")
    assert 'href="summary.md.txt"' in page and "hidden" not in page.lower().replace("hidden_passed", "")


def test_build_site_is_idempotent_and_uses_docs_by_default(tmp_path, canary_dir):
    _completed(tmp_path, canary_dir, date(2026, 10, 7))
    out1 = build_site(tmp_path)
    out2 = build_site(tmp_path)
    assert out1 == out2 == tmp_path / "docs"
    assert (out2 / "index.html").exists() and (out2 / ".nojekyll").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_dashboard.py -q`
Expected: `ModuleNotFoundError: No module named 'paper2code.dashboard'`.

- [ ] **Step 3: Implement**

`src/paper2code/dashboard/__init__.py`: empty.

`src/paper2code/dashboard/build.py`:

```python
"""Static dashboard over the runs root (spec 11): index of every run with outcome and cost, one page per
run with the record's files. Pure Python, no JavaScript, every string escaped; hidden tests are never
copied because the site may be public."""
from __future__ import annotations

import html
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from paper2code.manager.record import RunRecord
from paper2code.manager.verdict import VERDICT_JSON, Verdict

SITE_DIR = "docs"
_DRILLDOWN = ("run.json", "summary.md", "verdict.json", "build.log", "candidates.jsonl", "selected.json", "scope_attempts.jsonl",
              "scope/spec.md", "scope/interface.md", "scope/manifest.json")
_DRILLDOWN_GLOBS = ("scope/tests/public/*.py", "workspace/**/*.py")
_CSS = """
body{font-family:system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#222}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:.4rem .6rem;border-bottom:1px solid #ddd;vertical-align:top}
.badge{padding:.1rem .5rem;border-radius:.6rem;font-size:.85em;background:#eee}
.completed{background:#d8f3dc}.completed_suspicious{background:#fff3bf}.hidden_failed,.tests_tampered{background:#ffd6d6}
.incomplete_budget,.incomplete_stuck{background:#e7e5ff}.error,.unreadable{background:#f1f1f1}.scope_rejected,.no_candidates{background:#f8f0e3}
pre{background:#f6f6f6;padding:.8rem;overflow:auto}
"""


@dataclass
class RunRow:
    run_id: str
    run_dir: Path
    outcome: str
    stage: str
    paper_title: str
    paper_url: str
    arxiv_id: str
    spent_usd: float
    gpu_seconds: float
    test_runs_used: int
    flags: int
    confidence: float | None
    started_at: str
    finished_at: str
    error: str


def _esc(text: object) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def collect_runs(runs_root: Path) -> list[RunRow]:
    rows: list[RunRow] = []
    for run_dir in sorted((p for p in runs_root.iterdir() if p.is_dir() and (p / "run.json").exists()), reverse=True):
        try:
            rec = RunRecord.load(run_dir)
        except Exception as exc:  # a half-written or foreign run.json must not hide the other runs
            rows.append(RunRow(run_dir.name, run_dir, "unreadable", "", "", "", "", 0.0, 0.0, 0, 0, None, "", "", f"{type(exc).__name__}: {exc}"))
            continue
        flags, confidence = 0, None
        if (run_dir / VERDICT_JSON).exists():
            try:
                v = Verdict.load(run_dir)
                flags, confidence = len(v.flags), v.confidence
            except Exception:
                pass
        rows.append(RunRow(
            rec.run_id, run_dir, rec.outcome.value if rec.outcome else "in progress", rec.stage or "",
            rec.paper.title, rec.paper.url, rec.paper.arxiv_id, rec.budget.spent_usd, rec.budget.gpu_seconds,
            rec.counters.test_runs_used, flags, confidence, rec.started_at or "", rec.finished_at or "",
            f"{rec.error.stage}/{rec.error.reason}: {rec.error.message}" if rec.error else "",
        ))
    return rows


def _page(title: str, body: str) -> str:
    return f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>{_esc(title)}</title><style>{_CSS}</style></head><body>{body}</body></html>"


def _index_html(rows: list[RunRow]) -> str:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
    total = sum(r.spent_usd for r in rows)
    head = "".join(f"<span class=\"badge {_esc(k)}\">{_esc(k)}: {v}</span> " for k, v in sorted(counts.items()))
    lines = [f"<h1>paper2code runs</h1><p>{len(rows)} runs, {total:.2f} USD of model spend. {head}</p>",
             "<table><tr><th>run</th><th>paper</th><th>outcome</th><th>stage</th><th>USD</th><th>GPU s</th><th>test runs</th><th>flags</th><th>confidence</th></tr>"]
    for r in rows:
        paper = f"<a href=\"{_esc(r.paper_url)}\">{_esc(r.paper_title)}</a>" if r.paper_url else _esc(r.paper_title)
        conf = f"{r.confidence:.2f}" if r.confidence is not None else ""
        err = f"<br><small>{_esc(r.error)}</small>" if r.error else ""
        lines.append(
            f"<tr><td><a href=\"runs/{_esc(r.run_id)}/index.html\">{_esc(r.run_id)}</a></td><td>{paper}</td>"
            f"<td><span class=\"badge {_esc(r.outcome)}\">{_esc(r.outcome)}</span>{err}</td><td>{_esc(r.stage)}</td>"
            f"<td>{r.spent_usd:.2f}</td><td>{r.gpu_seconds:.0f}</td><td>{r.test_runs_used}</td><td>{r.flags}</td><td>{conf}</td></tr>"
        )
    lines.append("</table>")
    return _page("paper2code runs", "\n".join(lines))


def _copy_drilldown(run_dir: Path, out: Path) -> list[str]:
    copied: list[str] = []
    candidates = [run_dir / rel for rel in _DRILLDOWN]
    for pattern in _DRILLDOWN_GLOBS:
        candidates += sorted(run_dir.glob(pattern))
    for src in candidates:
        if not src.is_file() or "hidden" in src.relative_to(run_dir).parts:
            continue
        rel = src.relative_to(run_dir).as_posix() + ".txt"
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        copied.append(rel)
    return copied


def _run_html(row: RunRow, files: list[str]) -> str:
    parts = [f"<p><a href=\"../../index.html\">all runs</a></p><h1>Run {_esc(row.run_id)}</h1>",
             f"<p><span class=\"badge {_esc(row.outcome)}\">{_esc(row.outcome)}</span> stage {_esc(row.stage)} "
             f"| {row.spent_usd:.2f} USD | {row.gpu_seconds:.0f} GPU s | {row.test_runs_used} test runs</p>"]
    if row.paper_title or row.arxiv_id:
        link = f"<a href=\"{_esc(row.paper_url)}\">{_esc(row.paper_title)}</a>" if row.paper_url else _esc(row.paper_title)
        parts.append(f"<p>Paper: {link} ({_esc(row.arxiv_id)})</p>")
    if row.error:
        parts.append(f"<p><b>Error:</b> {_esc(row.error)}</p>")
    summary = row.run_dir / "summary.md"
    parts.append("<h2>Summary</h2>")
    parts.append(f"<pre>{_esc(summary.read_text(encoding='utf-8', errors='replace'))}</pre>" if summary.exists() else "<p>no summary (run ended before the report stage)</p>")
    parts.append("<h2>Verdict</h2>")
    if (row.run_dir / VERDICT_JSON).exists():
        try:
            v = Verdict.load(row.run_dir)
            parts.append(f"<p>{_esc(v.summary)}</p>")
            if v.confidence is not None:
                parts.append(f"<p>Inspector confidence: {v.confidence:.2f}</p>")
            parts.append(f"<p>Hidden tests: {len(v.hidden_passed)} passed, {len(v.hidden_failed)} failed</p>")
            if v.flags:
                parts.append("<ul>" + "".join(f"<li><code>{_esc(f.kind)}</code> ({_esc(f.source)}) at {_esc(f.file)}:{_esc(f.line)}: {_esc(f.note)}</li>" for f in v.flags) + "</ul>")
        except Exception as exc:
            parts.append(f"<p>verdict unreadable: {_esc(type(exc).__name__)}</p>")
    else:
        parts.append("<p>no verdict (run ended before inspection)</p>")
    parts.append("<h2>Files</h2><ul>" + "".join(f"<li><a href=\"{_esc(f)}\">{_esc(f[:-4])}</a></li>" for f in files) + "</ul>")
    return _page(f"Run {row.run_id}", "\n".join(parts))


def build_site(runs_root: Path, out_dir: Path | None = None) -> Path:
    out = out_dir or runs_root / SITE_DIR
    out.mkdir(parents=True, exist_ok=True)
    (out / ".nojekyll").write_text("", encoding="utf-8")  # GitHub Pages: serve files starting with _ or .
    rows = collect_runs(runs_root)
    (out / "index.html").write_text(_index_html(rows), encoding="utf-8")
    for row in rows:
        run_out = out / "runs" / row.run_id
        if run_out.exists():
            shutil.rmtree(run_out)
        run_out.mkdir(parents=True)
        files = _copy_drilldown(row.run_dir, run_out) if row.outcome != "unreadable" else []
        (run_out / "index.html").write_text(_run_html(row, files), encoding="utf-8")
    return out
```

(`collect_runs` must skip the `docs` directory itself: it has no `run.json`, so the `(p / "run.json").exists()` filter already does.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_dashboard.py -q`
Expected: all pass. Open `docs/index.html` from one of the test outputs in a browser once to eyeball it; note anything ugly in the ledger.

- [ ] **Step 5: Commit**

```bash
git add src/paper2code/dashboard tests/test_dashboard.py
git commit -m "Dashboard: static index and per-run pages over the runs root, escaped, hidden tests never copied

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Preflight, the stage hook, and `paper2code daily`

**Files:**
- Create: `src/paper2code/manager/preflight.py`, `src/paper2code/manager/daily.py`, `src/paper2code/manager/notify.py`
- Modify: `src/paper2code/manager/graph.py` (`RunContext.on_stage_done`), `src/paper2code/manager/record.py` (`create_run` suffix), `src/paper2code/cli.py` (`daily`, `preflight`, `dashboard` commands)
- Test: `tests/test_preflight.py`, `tests/test_daily.py`, `tests/test_graph.py`, `tests/test_record.py` (or wherever `create_run` is tested), `tests/test_cli.py`

**Interfaces:**
- Consumes: `RunsRepo`, `build_site`, `run_pipeline`, `make_chat_model`, `find_cli`, `modal.Function.from_name` (lazily), `httpx`.
- Produces:
  - `RunContext.on_stage_done: Callable[[RunRecord, str], None] | None = None`; `make_node` calls it after the record is saved (both the normal path and the `api_error` path), inside a try/except that records a build-log-free warning: exceptions from the hook are collected on the context? No: the hook is the daily command's publish, which handles its own errors and never raises. `make_node` calls it plainly.
  - `create_run(runs_root, today, caps, limit_usd)`: when `runs_root/<date>` exists, use `<date>-2`, `<date>-3`, ... (first free). `run_id` is the directory name.
  - `preflight.py`: `@dataclass Check(name: str, ok: bool, detail: str)`; `run_preflight(config, *, llm: str, no_gpu: bool, builder: str, publish: bool, modal_lookup=None, cli_finder=None, env=os.environ, git_probe=None) -> list[Check]` with checks: `openai_key` (env has `OPENAI_API_KEY`; skipped when `llm != "openai"`), `claude_cli` (`cli_finder()` returns a path, or the SDK's bundled CLI exists: `Path(claude_agent_sdk.__file__).parent / "_bundled" / ("claude.exe" on Windows else "claude")`; skipped when `builder != "agent"`), `anthropic_key_absent` (env must not define `ANTHROPIC_API_KEY`; when it does, ok=False with detail "would silently override the subscription"), `gpu_function` (`modal_lookup(config.modal_app_name, "run_tests_remote")` succeeds; skipped when `no_gpu`), `timeouts` (`config.run_tests_timeout_s < config.test_function_timeout_s`), `runs_repo` (`git_probe(config.runs_repo_url)` i.e. `git ls-remote --exit-code <authed url> HEAD` returns 0 or the repo is empty; skipped when not `publish`), `github_token` (env has `GITHUB_TOKEN` or the URL is not https; skipped when not publish). `all_ok(checks) -> bool`. `format_checks(checks) -> str` one line each: `ok  openai_key: present` / `FAIL gpu_function: ...` / `skip claude_cli: builder is stub`. Details never include secret values.
  - `notify.py`: `notify(url: str, payload: dict, post=None) -> bool`: no-op returning False when `url` is empty; otherwise POST JSON with a 10 s timeout, returns True on 2xx, False otherwise; never raises.
  - `daily.py`: `@dataclass DailyResult(run_dir: Path | None, record: RunRecord | None, checks: list[Check], published: bool, publish_error: str, site: Path | None, notified: bool)`; `run_daily(config, *, llm, no_gpu, builder, reference_dir, today, publish, runs_repo_factory=RunsRepo, site_builder=build_site, pipeline=run_pipeline, preflight=run_preflight, notifier=notify, stdout=print) -> DailyResult`:
    1. `checks = preflight(...)`; print them; if not all ok → return with `run_dir=None` (nothing created, nothing spent).
    2. `repo = runs_repo_factory(config.runs_root, config.runs_repo_url if publish else "")`; `repo.ensure()` when publish (a `GitError` here is fatal: return with `publish_error` and no run).
    3. `record = create_run(...)`; define `hook(record, stage)`: `repo.publish([record.run_dir, runs_root/"seen.jsonl"], f"run {record.run_id}: {stage}")` wrapped so `GitError` is remembered in `publish_error` and printed, never raised.
    4. `pipeline(run_dir, RunContext(..., on_stage_done=hook if publish else None))`.
    5. `site = site_builder(config.runs_root)`; `repo.publish([site, record.run_dir], f"run {record.run_id}: dashboard")` (same error handling).
    6. `notified = notifier(config.notify_url, {"run_id", "outcome", "paper", "spent_usd", "gpu_seconds", "flags"?...})` with the fields from the record and verdict.
    7. Return the result; the CLI exit code is 0 when the pipeline finished (whatever the outcome) and publishing succeeded or was off, 2 when preflight failed, 1 when the pipeline raised or a publish failed.
  - CLI: `paper2code daily [--llm] [--no-gpu] [--builder] [--reference] [--no-publish] [--date] [--config]`; `paper2code preflight [same flags]` prints checks, exit 0/2; `paper2code dashboard [--runs-root] [--out]` builds the site.

- [ ] **Step 1: Write the failing tests**

`tests/test_preflight.py`:

```python
from pathlib import Path

from paper2code.config import Config
from paper2code.manager.preflight import all_ok, format_checks, run_preflight


def _names(checks, ok=None):
    return [c.name for c in checks if ok is None or c.ok is ok]


def test_preflight_local_fake_needs_nothing(tmp_path):
    checks = run_preflight(Config(runs_root=tmp_path), llm="fake", no_gpu=True, builder="stub", publish=False, env={})
    assert all_ok(checks)
    assert "skip" in format_checks(checks)


def test_preflight_openai_requires_key_and_no_anthropic_key(tmp_path):
    checks = run_preflight(Config(runs_root=tmp_path), llm="openai", no_gpu=True, builder="stub", publish=False, env={})
    assert not all_ok(checks) and "openai_key" in _names(checks, ok=False)
    bad = run_preflight(Config(runs_root=tmp_path), llm="fake", no_gpu=True, builder="agent", publish=False,
                        env={"ANTHROPIC_API_KEY": "sk-ant-should-not-be-here"}, cli_finder=lambda: "C:/claude.exe")
    assert "anthropic_key_absent" in _names(bad, ok=False)
    assert "sk-ant" not in format_checks(bad)


def test_preflight_agent_builder_accepts_bundled_cli(tmp_path):
    checks = run_preflight(Config(runs_root=tmp_path), llm="fake", no_gpu=True, builder="agent", publish=False, env={}, cli_finder=lambda: None)
    cli = next(c for c in checks if c.name == "claude_cli")
    assert cli.ok and "bundled" in cli.detail  # the installed SDK wheel bundles the CLI on this machine


def test_preflight_gpu_function_and_timeouts(tmp_path):
    cfg = Config(runs_root=tmp_path, run_tests_timeout_s=900, test_function_timeout_s=1800)
    ok = run_preflight(cfg, llm="fake", no_gpu=False, builder="stub", publish=False, env={}, modal_lookup=lambda app, fn: object())
    assert all_ok(ok)

    def missing(app, fn):
        raise LookupError("not deployed")

    bad = run_preflight(cfg, llm="fake", no_gpu=False, builder="stub", publish=False, env={}, modal_lookup=missing)
    assert "gpu_function" in _names(bad, ok=False) and "not deployed" in format_checks(bad)
    slow = run_preflight(Config(runs_root=tmp_path, run_tests_timeout_s=2000, test_function_timeout_s=1800), llm="fake", no_gpu=True, builder="stub", publish=False, env={})
    assert "timeouts" in _names(slow, ok=False)


def test_preflight_publishing_needs_remote_and_token(tmp_path):
    cfg = Config(runs_root=tmp_path, runs_repo_url="https://github.com/x/runs")
    checks = run_preflight(cfg, llm="fake", no_gpu=True, builder="stub", publish=True, env={}, git_probe=lambda url: True)
    assert "github_token" in _names(checks, ok=False)
    checks = run_preflight(cfg, llm="fake", no_gpu=True, builder="stub", publish=True, env={"GITHUB_TOKEN": "t"}, git_probe=lambda url: False)
    assert "runs_repo" in _names(checks, ok=False)
    checks = run_preflight(Config(runs_root=tmp_path), llm="fake", no_gpu=True, builder="stub", publish=True, env={})
    assert "runs_repo" in _names(checks, ok=False) and "runs_repo_url" in format_checks(checks)
```

`tests/test_daily.py`:

```python
"""`paper2code daily` with the fake model, the stub builder, and a local bare repository as the remote."""
import subprocess
from datetime import date
from pathlib import Path

from paper2code.cli import main
from paper2code.config import Config
from paper2code.manager.daily import run_daily
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord
from paper2code.manager.runs_repo import GitError, RunsRepo
from tests.test_cli import _patch_http


def _bare(tmp_path):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(bare)], check=True, capture_output=True, stdin=subprocess.DEVNULL)
    return bare


def _cfg(tmp_path, **kw):
    return Config(runs_root=tmp_path / "runs", run_tests_timeout_s=120, **kw)


def _daily(cfg, canary_dir, **kw):
    args = dict(llm="fake", no_gpu=True, builder="stub", reference_dir=canary_dir / "reference", today=date(2026, 10, 7), publish=True)
    args.update(kw)
    return run_daily(cfg, **args)


def test_daily_runs_publishes_after_every_stage_and_builds_the_dashboard(tmp_path, canary_dir, monkeypatch):
    _patch_http(monkeypatch)
    bare = _bare(tmp_path)
    res = _daily(_cfg(tmp_path, runs_repo_url=str(bare)), canary_dir)
    assert res.record.outcome is Outcome.COMPLETED and res.published and res.publish_error == ""
    log = subprocess.run(["git", "log", "--format=%s", "main"], cwd=bare, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.splitlines()
    assert log[0] == "run 2026-10-07: dashboard"
    assert [m for m in log if m.startswith("run 2026-10-07: ")][::-1][:7] == [f"run 2026-10-07: {s}" for s in ("fetch", "score", "select", "scope", "build", "inspect", "report")]
    assert (res.site / "index.html").exists() and (tmp_path / "runs" / "docs" / "runs" / "2026-10-07" / "index.html").exists()
    files = subprocess.run(["git", "ls-tree", "-r", "--name-only", "main"], cwd=bare, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    assert "seen.jsonl" in files and "2026-10-07/run.json" in files and "docs/index.html" in files
    assert "scope/tests/hidden" in files  # the record keeps the hidden tests; only the dashboard copy omits them


def test_daily_second_run_same_day_gets_a_suffix(tmp_path, canary_dir, monkeypatch):
    _patch_http(monkeypatch)
    cfg = _cfg(tmp_path)
    first = _daily(cfg, canary_dir, publish=False)
    second = _daily(cfg, canary_dir, publish=False)
    assert first.run_dir.name == "2026-10-07" and second.run_dir.name == "2026-10-07-2"
    assert RunRecord.load(second.run_dir).run_id == "2026-10-07-2"
    assert second.record.outcome is Outcome.NO_CANDIDATES  # everything from the feed was graded by the first run


def test_daily_refuses_when_preflight_fails(tmp_path, canary_dir, monkeypatch):
    _patch_http(monkeypatch)
    calls = []
    res = run_daily(_cfg(tmp_path), llm="openai", no_gpu=True, builder="stub", reference_dir=canary_dir / "reference", today=date(2026, 10, 7),
                    publish=False, pipeline=lambda *a, **k: calls.append(a), env={})
    assert res.run_dir is None and not calls and not (tmp_path / "runs" / "2026-10-07").exists()
    assert any(c.name == "openai_key" and not c.ok for c in res.checks)


def test_daily_finishes_the_run_when_publish_fails(tmp_path, canary_dir, monkeypatch):
    _patch_http(monkeypatch)
    res = _daily(_cfg(tmp_path, runs_repo_url=str(tmp_path / "missing.git")), canary_dir)
    assert res.record.outcome is Outcome.COMPLETED and res.run_dir.exists()
    assert not res.published and "missing.git" in res.publish_error or "failed" in res.publish_error
    # every stage was still committed locally
    log = subprocess.run(["git", "log", "--format=%s"], cwd=tmp_path / "runs", capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    assert "run 2026-10-07: report" in log and "run 2026-10-07: dashboard" in log


def test_daily_notifies_when_configured(tmp_path, canary_dir, monkeypatch):
    _patch_http(monkeypatch)
    seen = {}
    res = _daily(_cfg(tmp_path, notify_url="https://hooks.invalid/x"), canary_dir, publish=False, notifier=lambda url, payload: seen.update(url=url, payload=payload) or True)
    assert res.notified and seen["url"] == "https://hooks.invalid/x" and seen["payload"]["outcome"] == "completed" and seen["payload"]["run_id"] == "2026-10-07"


def test_cli_daily_preflight_and_dashboard(tmp_path, canary_dir, monkeypatch, capsys):
    _patch_http(monkeypatch)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"runs_root: {(tmp_path / 'runs').as_posix()}\nrun_tests_timeout_s: 120\n", encoding="utf-8")
    assert main(["preflight", "--config", str(cfg), "--llm", "fake", "--no-gpu", "--builder", "stub", "--no-publish"]) == 0
    assert main(["preflight", "--config", str(cfg), "--llm", "openai", "--no-gpu", "--builder", "stub", "--no-publish"]) == 2
    rc = main(["daily", "--config", str(cfg), "--llm", "fake", "--no-gpu", "--builder", "stub", "--reference", str(canary_dir / "reference"),
               "--no-publish", "--date", "2026-10-07"])
    assert rc == 0 and "outcome: completed" in capsys.readouterr().out
    assert main(["dashboard", "--config", str(cfg)]) == 0 and (tmp_path / "runs" / "docs" / "index.html").exists()
```

Append to `tests/test_graph.py`:

```python
def test_on_stage_done_hook_is_called_after_each_stage_with_the_saved_record(tmp_path, canary_dir):
    from paper2code.manager.local import init_run
    from paper2code.manager.record import Paper
    from paper2code.config import Config as _Config
    from datetime import date as _date

    rec = init_run(tmp_path, canary_dir / "scope", Paper("canary-0001", "t", ""), _date(2026, 10, 7), _Config(runs_root=tmp_path))
    seen = []
    ctx = RunContext(config=_Config(runs_root=tmp_path, run_tests_timeout_s=120), builder="stub", reference_dir=canary_dir / "reference", llm="fake",
                     on_stage_done=lambda record, stage: seen.append((stage, record.stage, RunRecord.load(record.run_dir).stage)))
    run_pipeline(rec.run_dir, ctx)
    assert seen == [("build", "build", "build"), ("inspect", "inspect", "inspect"), ("report", "report", "report")]
```

(Check the top of `tests/test_graph.py` for its imports of `RunContext`, `run_pipeline`, `RunRecord`; add what is missing.)

Append to `tests/test_record.py` (or the file that tests `create_run`; find it with `grep -ln "create_run" tests/*.py`):

```python
def test_create_run_suffixes_a_second_run_on_the_same_day(tmp_path):
    a = create_run(tmp_path, date(2026, 10, 7), Caps(), 10.0)
    b = create_run(tmp_path, date(2026, 10, 7), Caps(), 10.0)
    c = create_run(tmp_path, date(2026, 10, 7), Caps(), 10.0)
    assert (a.run_id, b.run_id, c.run_id) == ("2026-10-07", "2026-10-07-2", "2026-10-07-3")
    assert b.run_dir == tmp_path / "2026-10-07-2" and RunRecord.load(b.run_dir).run_id == "2026-10-07-2"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_preflight.py tests/test_daily.py tests/test_graph.py tests/test_record.py -q`
Expected: `ModuleNotFoundError` for `preflight` and `daily`; the graph test fails with `TypeError: unexpected keyword argument 'on_stage_done'`; the record test fails on `create_run` raising or overwriting (read what it does today).

- [ ] **Step 3: Implement**

`graph.py`: add `on_stage_done: Callable[[RunRecord, str], None] | None = None` to `RunContext` (import `Callable` already there). In `make_node`, after `record.save()` on the normal path and on the `api_error` path, call `if ctx.on_stage_done: ctx.on_stage_done(record, stage)`. Not on the crash path (the exception propagates; the next `daily` publishes what is there).

`record.py` `create_run`: compute `base = today.isoformat()`; `run_id = base`; `n = 2`; `while (runs_root / run_id).exists(): run_id = f"{base}-{n}"; n += 1`; use `run_id` for both the directory and the record's `run_id`. Read the current function first and keep everything else as is.

`src/paper2code/manager/notify.py`:

```python
"""Optional notification at the end of a daily run: one JSON POST. Off unless notify_url is set; never raises."""
from __future__ import annotations

import httpx


def notify(url: str, payload: dict, post=None) -> bool:
    if not url:
        return False
    post = post or (lambda u, p: httpx.post(u, json=p, timeout=10.0))
    try:
        resp = post(url, payload)
        return 200 <= getattr(resp, "status_code", 0) < 300
    except Exception:
        return False
```

`src/paper2code/manager/preflight.py`:

```python
"""Checks that cost nothing and must pass before a daily run spends anything. No secret value ever
appears in a check's detail."""
from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from paper2code.config import Config


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    skipped: bool = False


def _bundled_cli() -> Path | None:
    try:
        import claude_agent_sdk
    except ImportError:
        return None
    name = "claude.exe" if platform.system() == "Windows" else "claude"
    path = Path(claude_agent_sdk.__file__).parent / "_bundled" / name
    return path if path.is_file() else None


def _default_modal_lookup(app_name: str, function_name: str):
    import modal

    return modal.Function.from_name(app_name, function_name).hydrate()


def _default_git_probe(url: str) -> bool:
    from paper2code.manager.runs_repo import RunsRepo

    authed = RunsRepo(Path("."), remote_url=url)._authed_url()
    proc = subprocess.run(["git", "ls-remote", authed, "HEAD"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return proc.returncode == 0


def run_preflight(
    config: Config, *, llm: str, no_gpu: bool, builder: str, publish: bool,
    modal_lookup: Callable | None = None, cli_finder: Callable[[], str | None] | None = None,
    env: Mapping[str, str] | None = None, git_probe: Callable[[str], bool] | None = None,
) -> list[Check]:
    env = os.environ if env is None else env
    checks: list[Check] = []

    if llm == "openai":
        checks.append(Check("openai_key", bool(env.get("OPENAI_API_KEY")), "present" if env.get("OPENAI_API_KEY") else "OPENAI_API_KEY is not set"))
    else:
        checks.append(Check("openai_key", True, f"llm is {llm}", skipped=True))

    if builder == "agent":
        from paper2code.agents.builder.agent import find_cli

        found = (cli_finder or find_cli)()
        bundled = _bundled_cli()
        if found:
            checks.append(Check("claude_cli", True, f"found at {found}"))
        elif bundled:
            checks.append(Check("claude_cli", True, f"bundled with the Agent SDK at {bundled}"))
        else:
            checks.append(Check("claude_cli", False, "no claude executable on PATH, CLAUDE_CODE_EXECPATH, or bundled with the SDK"))
        checks.append(Check("anthropic_key_absent", "ANTHROPIC_API_KEY" not in env,
                            "absent" if "ANTHROPIC_API_KEY" not in env else "ANTHROPIC_API_KEY is set; it would silently override the subscription"))
    else:
        checks.append(Check("claude_cli", True, f"builder is {builder}", skipped=True))

    if no_gpu:
        checks.append(Check("gpu_function", True, "--no-gpu", skipped=True))
    else:
        try:
            (modal_lookup or _default_modal_lookup)(config.modal_app_name, "run_tests_remote")
            checks.append(Check("gpu_function", True, f"{config.modal_app_name}/run_tests_remote is deployed"))
        except Exception as exc:
            checks.append(Check("gpu_function", False, f"{type(exc).__name__}: {str(exc)[:200]}; run `python -m modal deploy src/paper2code/sandbox/modal_app.py`"))

    ok = config.run_tests_timeout_s < config.test_function_timeout_s
    checks.append(Check("timeouts", ok, f"run_tests_timeout_s={config.run_tests_timeout_s} {'<' if ok else '>='} test_function_timeout_s={config.test_function_timeout_s}"))

    if not publish:
        checks.append(Check("runs_repo", True, "publishing off", skipped=True))
    elif not config.runs_repo_url:
        checks.append(Check("runs_repo", False, "runs_repo_url is empty; set it in config.yaml or pass --no-publish"))
    else:
        if config.runs_repo_url.startswith("https://") and not env.get("GITHUB_TOKEN"):
            checks.append(Check("github_token", False, "GITHUB_TOKEN is not set; pushes to an https remote need it"))
        reachable = (git_probe or _default_git_probe)(config.runs_repo_url)
        checks.append(Check("runs_repo", reachable, "reachable" if reachable else f"cannot reach {config.runs_repo_url}"))
    return checks


def all_ok(checks: list[Check]) -> bool:
    return all(c.ok for c in checks)


def format_checks(checks: list[Check]) -> str:
    return "\n".join(f"{'skip' if c.skipped else 'ok  ' if c.ok else 'FAIL'} {c.name}: {c.detail}" for c in checks)
```

(`_default_git_probe` passes the token inside the URL argument to `git ls-remote`; the command's output is not logged.)

`src/paper2code/manager/daily.py`:

```python
"""One unattended day: preflight, create the run, run the pipeline publishing after every stage,
rebuild the dashboard, publish, notify. Nothing here changes what a stage does."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from paper2code.config import Config
from paper2code.dashboard.build import build_site
from paper2code.manager.graph import RunContext, run_pipeline
from paper2code.manager.notify import notify
from paper2code.manager.preflight import Check, all_ok, format_checks, run_preflight
from paper2code.manager.record import Caps, RunRecord, create_run
from paper2code.manager.runs_repo import GitError, RunsRepo
from paper2code.manager.seen import SEEN_FILE
from paper2code.manager.verdict import VERDICT_JSON, Verdict


@dataclass
class DailyResult:
    run_dir: Path | None = None
    record: RunRecord | None = None
    checks: list[Check] = field(default_factory=list)
    published: bool = False
    publish_error: str = ""
    site: Path | None = None
    notified: bool = False


def _caps(config: Config) -> Caps:
    return Caps(test_runs=config.caps.test_runs, wall_clock_s=config.caps.wall_clock_s, stall_n=config.caps.stall_n)


def _payload(record: RunRecord) -> dict:
    flags = 0
    if (record.run_dir / VERDICT_JSON).exists():
        try:
            flags = len(Verdict.load(record.run_dir).flags)
        except Exception:
            flags = -1
    return {
        "run_id": record.run_id, "outcome": record.outcome.value if record.outcome else None, "stage": record.stage,
        "paper": {"arxiv_id": record.paper.arxiv_id, "title": record.paper.title, "url": record.paper.url},
        "spent_usd": round(record.budget.spent_usd, 4), "gpu_seconds": round(record.budget.gpu_seconds, 1),
        "test_runs_used": record.counters.test_runs_used, "flags": flags,
        "error": {"stage": record.error.stage, "reason": record.error.reason} if record.error else None,
    }


def run_daily(
    config: Config, *, llm: str, no_gpu: bool, builder: str, reference_dir: Path | None, today: date, publish: bool,
    runs_repo_factory: Callable[..., RunsRepo] = RunsRepo, site_builder=build_site, pipeline=run_pipeline,
    preflight=run_preflight, notifier=notify, stdout=print, env=None,
) -> DailyResult:
    result = DailyResult()
    kwargs = {"env": env} if env is not None else {}
    result.checks = preflight(config, llm=llm, no_gpu=no_gpu, builder=builder, publish=publish, **kwargs)
    stdout(format_checks(result.checks))
    if not all_ok(result.checks):
        stdout("preflight failed; nothing was started")
        return result

    repo = runs_repo_factory(config.runs_root, config.runs_repo_url if publish else "")
    if publish:
        try:
            repo.ensure()
        except GitError as exc:
            result.publish_error = str(exc)
            stdout(f"runs repository unavailable: {exc}")
            return result

    record = create_run(config.runs_root, today, _caps(config), config.budget.limit_usd)
    result.run_dir, result.record = record.run_dir, record
    stdout(f"created {record.run_dir}")

    def _publish(paths: list[Path], message: str) -> None:
        if not publish:
            return
        try:
            repo.publish([p for p in paths if p.exists()], message)
            result.published = True
        except GitError as exc:
            result.published = False
            result.publish_error = str(exc)
            stdout(f"publish failed ({message}): {exc}")

    def hook(rec: RunRecord, stage: str) -> None:
        _publish([rec.run_dir, config.runs_root / SEEN_FILE], f"run {rec.run_id}: {stage}")

    ctx = RunContext(
        config=config, no_gpu=no_gpu, builder=builder, reference_dir=reference_dir, llm=llm,
        on_stage_done=hook if publish else None,
    )
    try:
        result.record = pipeline(record.run_dir, ctx)
    finally:
        result.site = site_builder(config.runs_root)
        _publish([result.site, record.run_dir, config.runs_root / SEEN_FILE], f"run {record.run_id}: dashboard")
        record = RunRecord.load(record.run_dir)
        result.record = record
    result.notified = notifier(config.notify_url, _payload(record))
    return result
```

(`publish` sets `result.published = True` only on success and a later failure flips it back; `publish_error` keeps the last failure. The `finally` rebuilds the site even when a stage crashed, so a crash is visible on the dashboard.)

`cli.py`: add three subcommands.

```python
    daily = sub.add_parser("daily", help="one unattended run: preflight, run, publish after every stage, dashboard, notify")
    _add_run_args(daily)
    daily.add_argument("--no-publish", action="store_true", help="do not commit or push the runs repository")
    daily.add_argument("--date", type=date.fromisoformat, default=None)

    pre = sub.add_parser("preflight", help="check keys, CLI, GPU function, timeouts and the runs repository; spends nothing")
    _add_run_args(pre)
    pre.add_argument("--no-publish", action="store_true")

    dash = sub.add_parser("dashboard", help="rebuild the static dashboard under <runs_root>/docs")
    dash.add_argument("--runs-root", type=Path)
    dash.add_argument("--out", type=Path, default=None)
    _add_common(dash)
```

and in `main`, before the existing `until = ...` line:

```python
    if args.command == "dashboard":
        config = _load_config(args.config)
        out = build_site(args.runs_root or config.runs_root, args.out)
        print(f"dashboard written to {out}")
        return 0
    if args.command in ("daily", "preflight"):
        config = _load_config(args.config)
        llm = args.llm or config.llm
        builder = args.builder if args.builder is not None else config.daily_builder
        if builder == "stub" and args.reference is None:
            parser.error("--builder stub requires --reference DIR")
        if args.command == "preflight":
            checks = run_preflight(config, llm=llm, no_gpu=args.no_gpu, builder=builder, publish=not args.no_publish)
            print(format_checks(checks))
            return 0 if all_ok(checks) else 2
        try:
            res = run_daily(config, llm=llm, no_gpu=args.no_gpu, builder=builder, reference_dir=args.reference.resolve() if args.reference else None,
                            today=args.date or date.today(), publish=not args.no_publish)
        except Exception as exc:
            print(f"daily failed: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return 1
        if res.run_dir is None:
            return 2
        outcome = res.record.outcome.value if res.record and res.record.outcome else "none"
        print(f"stage: {res.record.stage}  outcome: {outcome}")
        if res.publish_error:
            print(f"publish failed: {res.publish_error}", file=sys.stderr)
            return 1
        return 0
```

For `daily`/`preflight`, `--builder` must default to `None` so the config's `daily_builder` applies: change `_add_run_args` to `default=None` for `--builder` and make `_context` use `args.builder or "stub"` so the other commands keep their old default. Imports: `from paper2code.dashboard.build import build_site`, `from paper2code.manager.daily import run_daily`, `from paper2code.manager.preflight import all_ok, format_checks, run_preflight`.

- [ ] **Step 4: Run the tests to verify they pass, then the whole suite**

Run: `pytest tests/test_preflight.py tests/test_daily.py tests/test_graph.py tests/test_record.py tests/test_cli.py -q` then `pytest -q`
Expected: all pass. The `daily` test with the real fake pipeline takes about a minute (stub check, build, inspect).

- [ ] **Step 5: Commit**

```bash
git add src/paper2code/manager/preflight.py src/paper2code/manager/daily.py src/paper2code/manager/notify.py src/paper2code/manager/graph.py src/paper2code/manager/record.py src/paper2code/cli.py tests/test_preflight.py tests/test_daily.py tests/test_graph.py tests/test_record.py
git commit -m "paper2code daily: preflight, run with publish after every stage, dashboard, notification; same-day run suffixes

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The scheduled Modal function

**Files:**
- Modify: `src/paper2code/sandbox/modal_app.py`
- Test: `tests/test_modal_runner.py` (source assertions, as before)

**Interfaces:**
- Produces in `modal_app.py`:
  - `SCHEDULE = os.environ.get("PAPER2CODE_SCHEDULE", "0 13 * * *")`, `MANAGER_TIMEOUT_S = int(os.environ.get("PAPER2CODE_MANAGER_TIMEOUT", str(6 * 3600)))`, `SECRET_NAME = os.environ.get("PAPER2CODE_SECRET", "paper2code")`.
  - `manager_image = modal.Image.debian_slim(python_version="3.12").apt_install("git").pip_install_from_pyproject("pyproject.toml").add_local_python_source("paper2code").add_local_file("config.yaml", "/root/config.yaml")`. (`pip_install_from_pyproject` installs the dependencies listed in the project; the SDK's Linux wheel brings the bundled CLI.)
  - `@app.function(name="daily_run", image=manager_image, schedule=modal.Cron(SCHEDULE, timezone="UTC"), secrets=[modal.Secret.from_name(SECRET_NAME, required_keys=["OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"])], timeout=MANAGER_TIMEOUT_S, cpu=2.0, memory=4096) def daily_run() -> int`: `os.chdir("/root")`, ensure `ANTHROPIC_API_KEY` is absent from `os.environ`, and return `paper2code.cli.main(["daily", "--config", "/root/config.yaml"])`. A missing `GITHUB_TOKEN` in the secret is fine when `runs_repo_url` is empty (preflight says so and refuses otherwise).
  - A module docstring paragraph on how to create the secret: `python -m modal secret create paper2code OPENAI_API_KEY=... CLAUDE_CODE_OAUTH_TOKEN=... GITHUB_TOKEN=...` (values from the shell, never typed into a file), and that `runs_repo_url` must be set in `config.yaml` before deploying for publishing to happen from the cloud.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_modal_runner.py`:

```python
def test_modal_app_declares_the_scheduled_manager():
    from pathlib import Path

    src = Path("src/paper2code/sandbox/modal_app.py").read_text(encoding="utf-8")
    assert 'name="daily_run"' in src and "modal.Cron(" in src and "Secret.from_name(" in src
    assert "pip_install_from_pyproject" in src and 'add_local_file("config.yaml"' in src
    assert '"OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"' in src and "ANTHROPIC_API_KEY" in src
    assert '["daily", "--config", "/root/config.yaml"]' in src
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_modal_runner.py -q`
Expected: the new test fails on `name="daily_run"`.

- [ ] **Step 3: Implement**

Append to `src/paper2code/sandbox/modal_app.py`:

```python
# ---- the scheduled manager (spec 12) ------------------------------------------------------------
# Secrets live in one Modal secret, created from the shell so no value is ever written to a file:
#   python -m modal secret create paper2code OPENAI_API_KEY=$OPENAI_API_KEY CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN GITHUB_TOKEN=$GITHUB_TOKEN
# ANTHROPIC_API_KEY is deliberately absent. Set runs_repo_url in config.yaml before deploying if the
# cloud run should push the runs repository; the schedule and timeout are deploy-time environment variables.
SCHEDULE = os.environ.get("PAPER2CODE_SCHEDULE", "0 13 * * *")
MANAGER_TIMEOUT_S = int(os.environ.get("PAPER2CODE_MANAGER_TIMEOUT", str(6 * 3600)))
SECRET_NAME = os.environ.get("PAPER2CODE_SECRET", "paper2code")

# The Agent SDK's Linux wheel bundles the claude CLI, so the builder needs no Node and no PATH entry here.
manager_image = (
    _base.pip_install_from_pyproject("pyproject.toml")
    .add_local_file("config.yaml", "/root/config.yaml")
    .add_local_python_source("paper2code")
)


@app.function(
    name="daily_run", image=manager_image, schedule=modal.Cron(SCHEDULE, timezone="UTC"),
    secrets=[modal.Secret.from_name(SECRET_NAME, required_keys=["OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"])],
    timeout=MANAGER_TIMEOUT_S, cpu=2.0, memory=4096,
)
def daily_run() -> int:
    from paper2code.cli import main

    os.environ.pop("ANTHROPIC_API_KEY", None)  # the subscription token must be the only Anthropic credential
    os.environ.setdefault("PYTHONUTF8", "1")
    os.chdir("/root")
    return main(["daily", "--config", "/root/config.yaml"])
```

Check that `pip_install_from_pyproject` exists on this Modal version (`python -c "import modal; print(hasattr(modal.Image, 'pip_install_from_pyproject'))"`); if not, list the dependencies from `pyproject.toml` in `pip_install(...)` explicitly and note it.

- [ ] **Step 4: Run the tests, then deploy and verify the function exists without triggering it**

Run: `pytest tests/test_modal_runner.py -q`. Then `PYTHONUTF8=1 python -m modal deploy src/paper2code/sandbox/modal_app.py`. Expected: `Created Function daily_run` alongside `run_tests_remote`, with the schedule shown in the dashboard. The deploy requires the secret to exist (`Secret.from_name` resolves at deploy time): create it first with the command from the docstring (the values come from the user environment; `GITHUB_TOKEN` may be omitted until a runs repository exists). Do NOT run `daily_run` from the cloud in this task; the schedule is live after deploy, so immediately after deploying, confirm with `python -m modal app list` and record the next run time in the ledger. If the author has not confirmed they want the cloud schedule active, deploy with `PAPER2CODE_SCHEDULE` left as is but `modal app stop`? No: simply do not deploy until Task 6's go-ahead; run the unit test only and record that the deploy is pending.

- [ ] **Step 5: Commit**

```bash
git add src/paper2code/sandbox/modal_app.py tests/test_modal_runner.py
git commit -m "Scheduled Modal function daily_run: manager image with the bundled CLI, one secret, cron from config

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Docs, journal, and the first unattended run

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `decisions.md`

- [ ] **Step 1: README and CLAUDE.md**

README Status: "Build step 6 of 6: `paper2code daily` runs one unattended day (preflight, run, publish after every stage to the runs repository, dashboard, optional notification) and a Modal scheduled function runs it daily. The dashboard is a static site under `<runs_root>/docs`." Add a "Daily run" section with: `paper2code preflight`, `paper2code daily --no-publish` (local, no runs repo), `paper2code daily` (publishes; needs `runs_repo_url` and `GITHUB_TOKEN`), `paper2code dashboard`, the secret creation command, `PYTHONUTF8=1 python -m modal deploy src/paper2code/sandbox/modal_app.py` with `PAPER2CODE_SCHEDULE`, and how to enable GitHub Pages on the runs repository (Settings → Pages → branch `main`, folder `/docs`). Replace "Local mode" commands' `runs/<date>` references where needed. CLAUDE.md: map lines for `manager/daily.py`, `manager/preflight.py`, `manager/runs_repo.py`, `dashboard/build.py`; "Build order ... 6 schedule, dashboard and the runs repository are done"; money note: "`paper2code daily` spends everything; `preflight` first; the Modal schedule runs it every day until `modal app stop paper2code`".

- [ ] **Step 2: The first real run (author's go-ahead required: it spends money)**

With the author's confirmation, run locally: `PYTHONUTF8=1 paper2code preflight --builder agent` (publishing on if a runs repository was configured, else `--no-publish`), then `PYTHONUTF8=1 paper2code daily [--no-publish]`. Record in the ledger: which paper was selected, each stage's duration, the outcome, spend in USD, GPU seconds, tokens, the verdict summary, and anything that went wrong. This is the project's first end-to-end unattended run; its record is the headline of the journal entry. If the author declines, run `paper2code daily --llm fake --no-gpu --builder stub --reference tests/fixtures/canary/reference --no-publish` as the live check instead and say so.

- [ ] **Step 3: Journal**

Append the step 6 entry to `decisions.md` from the ledger: what `daily` does, the publish-after-every-stage decision, the dashboard's public-safety rule (hidden tests never copied), the bundled-CLI fact that made the cloud manager simple, the preflight-before-spend rule, what the first real run did, and the open setup items the author owns (runs repository, GitHub token, Modal secret, schedule on/off, GitHub Pages).

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md decisions.md
git commit -m "Docs and journal for step 6: daily run, runs repository, dashboard, schedule

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

**Spec coverage.** 4 (run directory suffix, runs repo, push after every stage): Tasks 1 and 3. 5 (`seen.jsonl` at the runs root): already true; it is committed with every publish (Task 3). 11 (summary exists; commit and push; dashboard with index and drill-down; notifications off by default): Tasks 2 and 3. 12 (scheduled Modal function, secrets, no `ANTHROPIC_API_KEY`): Task 4. 13 (`dashboard/build.py`, `modal_app.py` scheduled function): Tasks 2 and 4. 14 (local mode unchanged): the new commands are additive. 16 step 6: Task 5's first run.

**Deviations recorded.** The runs repository is cloned shallow with `--depth 1` (spec says "cloned by the manager at start"; shallow is enough to append). The dashboard is written into the runs repository's `docs/` folder (so GitHub Pages can serve it with no extra hosting); the spec does not say where it lives. The inspector sandbox of spec 10.1 is still not created (decided in step 5). Secrets are one Modal secret rather than three.

**Type consistency.** `RunsRepo(root, remote_url)`, `publish(paths, message) -> bool`, `GitError` (Task 1) are what Task 3's `daily.py` and `preflight.py` use. `build_site(runs_root, out_dir=None) -> Path` (Task 2) matches `daily.py` and the CLI. `run_preflight(config, *, llm, no_gpu, builder, publish, modal_lookup, cli_finder, env, git_probe)` and `Check` match `daily.py` and the tests. `RunContext.on_stage_done(record, stage)` matches `make_node`'s call. `create_run` keeps its signature.

**Review Focus pinned.** 1 → Task 2 `test_dashboard_renders_runs_without_verdict_or_summary`; 2 → Task 2 `test_dashboard_escapes_untrusted_text`; 3 → Task 1 `test_publish_failure_leaves_the_repo_committed_and_reports` and Task 3 `test_daily_finishes_the_run_when_publish_fails`; 4 → Task 3 `test_daily_second_run_same_day_gets_a_suffix`; 5 → Task 3 `test_daily_refuses_when_preflight_fails`.
