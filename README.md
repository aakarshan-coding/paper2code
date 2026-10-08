# paper2code

A daily, unattended loop that picks one new arXiv paper, scopes it into a small
experiment with tests, implements it against those tests, and records what
happened. The record is the product. Design spec:
`docs/superpowers/specs/2026-09-30-paper2code-design.md`.

## Status

All six build steps are done. `paper2code daily` runs one unattended day: a preflight check that
spends nothing, then the seven stages, pushing the run directory to the runs repository after every
stage, then the static dashboard under `<runs_root>/docs`, then an optional notification. A Modal
scheduled function (`daily_run`) runs the same command once a day. The builder works in a Modal
sandbox and tests run in a Modal GPU function (`--no-gpu` keeps everything local); the inspector
reviews the code against the paper; the adversarial canaries live in
`tests/test_adversarial_canaries.py`.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

## Local mode

```bash
# Dry run: fetch today's papers, score them, pick a shortlist, stop.
paper2code new-run
paper2code run --run runs/<date> --until select            # real scout (OpenAI)
paper2code run --run runs/<date> --until select --llm fake # no model, plumbing only

# One stage at a time, or one paper at a time
paper2code fetch  --run runs/<date>
paper2code score  --run runs/<date>
paper2code score  --arxiv-id 2610.03769                     # fresh run with just this paper
paper2code select --run runs/<date>
paper2code scope  --run runs/<date>                         # draft, stub-check, freeze the top pick
paper2code scope  --arxiv-id 2610.07324                     # fresh run: score (forced eligible), select, scope
paper2code run --run runs/<date> --until scope --llm fake   # whole front half with the canned canary

# From a hand-written scope to completion (step 1 path)
paper2code init-run --scope tests/fixtures/canary/scope --paper-id canary-0001 --title "EMA denoising canary" --paper tests/fixtures/canary/paper.md
paper2code run --run runs/<date> --no-gpu --builder stub --reference tests/fixtures/canary/reference --llm fake   # fake inspector
paper2code run --run runs/<date> --no-gpu --builder stub --reference tests/fixtures/canary/reference              # real inspector (OpenAI)
PAPER2CODE_LIVE_INSPECT=1 pytest tests/test_live_inspector.py -q -s   # opt-in: the real inspector on the canaries
paper2code run --run runs/<date> --no-gpu --builder agent        # real agent, local workspace (no sandbox)
PAPER2CODE_LIVE_BUILD=1 pytest tests/test_live_agent.py -q -s     # opt-in live build of the canary
```

## Daily run

```bash
paper2code preflight                       # keys, claude CLI, GPU function, timeouts, runs repository; exit 2 on a problem
paper2code daily --no-publish              # one real day on this machine, no runs repository
paper2code daily                           # same, and push the run directory after every stage (needs runs_repo_url and GITHUB_TOKEN)
paper2code dashboard                       # rebuild <runs_root>/docs by hand
paper2code daily --llm fake --no-gpu --builder stub --reference tests/fixtures/canary/reference --no-publish   # offline rehearsal
```

The runs directory is its own git repository (`runs_repo_url` in `config.yaml`; empty means a
local repository with no remote). `seen.jsonl`, every run, and the dashboard live there. To serve
the dashboard with GitHub Pages: repository Settings, Pages, branch `main`, folder `/docs`. The
dashboard never copies the hidden tests, so the site can be public; the run directories in the
repository do contain them.

The scheduled cloud run needs one Modal secret and a deploy:

```bash
python -m modal secret create paper2code OPENAI_API_KEY=$OPENAI_API_KEY CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN GITHUB_TOKEN=$GITHUB_TOKEN
PYTHONUTF8=1 PAPER2CODE_SCHEDULE="0 13 * * *" python -m modal deploy src/paper2code/sandbox/modal_app.py
python -m modal app stop paper2code        # turns the schedule (and the GPU function) off
```

`daily_run` runs `paper2code daily` inside a Modal container that has the package, `config.yaml`,
and the Agent SDK's bundled `claude` binary. It spends every day it runs; preflight stops it when a
key, the GPU function or the runs repository is missing.

## Modal

Without `--no-gpu`, the agent works inside a Modal sandbox and every `run_tests` call (and the
inspector's hidden run) executes in a deployed Modal GPU function.

```bash
python -m modal setup                                           # once: login, writes ~/.modal.toml
python -m modal deploy src/paper2code/sandbox/modal_app.py      # once per change to the images or GPU type
PAPER2CODE_GPU=L4 python -m modal deploy src/paper2code/sandbox/modal_app.py   # a different GPU
paper2code run --run runs/<date> --builder agent                # agent in the sandbox, tests on the GPU
PAPER2CODE_LIVE_MODAL=1 pytest tests/test_live_modal.py -q -s   # opt-in live checks (a little GPU time)
```

On Windows, set `PYTHONUTF8=1` for the deploy; the CLI's progress output otherwise crashes on the
console encoding. Set a spend limit in the Modal dashboard; the per-run GPU budget in `config.yaml`
is a soft cap enforced from the manager's side.

A run directory holds `run.json`, `papers.jsonl`, `candidates.jsonl`,
`selected.json`, `scope_attempts.jsonl`, then `scope/` (frozen, with `manifest.json`), `workspace/`,
`build.log`, `verdict.json` and `summary.md`. `runs/seen.jsonl` lists every
paper ever graded. Re-running `run` on an existing run directory resumes at the
last completed stage. Set `PAPER2CODE_LIVE=1` to include the live arXiv smoke
test in `pytest`.

The agent builder uses the logged-in `claude` binary (or `CLAUDE_CODE_OAUTH_TOKEN` from
`claude setup-token` when unattended). Never set `ANTHROPIC_API_KEY`; it would silently override
the subscription. In local mode (`--no-gpu`) the agent's shell runs on this machine with
credentials scrubbed from its environment and paths confined to the workspace; use it only for
assignments you trust. Without the flag it runs in a Modal sandbox and cannot reach this machine
at all.

## Known limitations at this build step

- Workspace code runs in the same interpreter as pytest. The runner imports
  pytest before the workspace is on `sys.path`, runs in isolated mode, and
  registers a witness hook that records what each test actually did, so a
  workspace that patches pytest's reporting is marked failed by the runner and
  flagged by the inspector's workspace scan (`test_detection`). Deeper patches
  of pytest's runner internals remain an inspector concern.
- The inspector's code review is one model call over text; it executes
  nothing. Its flags are limited by schema to the spec's five kinds, but its
  judgment is a model's judgment: a flag is a reason to look, not a proof.
- In local mode nothing stops a builder from writing into `scope/`, but the
  manifest hash and the passing-workspace hash are anchored in `run.json`,
  which the builder never receives, so edits, re-freezes and post-pass
  workspace drift all end as `tests_tampered`. In Modal mode the builder
  cannot see `scope/` at all.
- GPU seconds are measured from the manager's side of each remote call, so
  they include upload and cold start; the figure is an upper bound on what
  Modal bills, which is the right direction for a budget cap.
