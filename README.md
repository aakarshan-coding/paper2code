# paper2code

A daily, unattended loop that picks one new arXiv paper, scopes it into a small
experiment with tests, implements it against those tests, and records what
happened. The record is the product. Design spec:
`docs/superpowers/specs/2026-09-30-paper2code-design.md`.

## Status

Build step 5 of 6: the inspector reviews the builder's code against the paper (one structured
call on the inspector model, flags limited to the spec's fixed checklist), two mechanical reviews
flag hidden-test probing, shrinking test counts and test-harness detection, and the test runner
itself catches in-process report tampering through a witness hook. The adversarial canaries from
the spec live in `tests/test_adversarial_canaries.py`. The builder runs in a Modal sandbox and
tests run in a Modal GPU function (`--no-gpu` keeps everything local). Step 6 is the schedule and
dashboard.

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
