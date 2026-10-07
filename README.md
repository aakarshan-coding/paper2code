# paper2code

A daily, unattended loop that picks one new arXiv paper, scopes it into a small
experiment with tests, implements it against those tests, and records what
happened. The record is the product. Design spec:
`docs/superpowers/specs/2026-09-30-paper2code-design.md`.

## Status

Build step 2 of 6: fetch, scout and select work against live arXiv in dry-run
mode. The scope stage is not implemented yet; a full run still needs `init-run`
with a hand-written scope directory. Scout calls go to OpenAI and need credits
on the account; `--llm fake` exercises the plumbing without any model.

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

# From a hand-written scope to completion (step 1 path)
paper2code init-run --scope tests/fixtures/canary/scope --paper-id canary-0001 --title "EMA denoising canary"
paper2code run --run runs/<date> --no-gpu --builder stub --reference tests/fixtures/canary/reference
```

A run directory holds `run.json`, `papers.jsonl`, `candidates.jsonl`,
`selected.json`, then `scope/` (frozen, with `manifest.json`), `workspace/`,
`build.log`, `verdict.json` and `summary.md`. `runs/seen.jsonl` lists every
paper ever graded. Re-running `run` on an existing run directory resumes at the
last completed stage. Set `PAPER2CODE_LIVE=1` to include the live arXiv smoke
test in `pytest`.

## Known limitations at this build step

- The test runner imports pytest before the workspace is on `sys.path` and runs
  the interpreter in isolated mode, so a workspace `pytest.py` or
  `sitecustomize.py` cannot replace the runner. A workspace module can still
  register a pytest plugin at import time that rewrites test reports in
  process. Closing that needs the stub check (step 3, records the expected
  test-id set at freeze) and the inspector's code review (step 5). Until then
  in-process execution is the trust boundary.
- In local mode nothing stops a builder from writing into `scope/`, but the
  manifest hash and the passing-workspace hash are anchored in `run.json`,
  which the builder never receives, so edits, re-freezes and post-pass
  workspace drift all end as `tests_tampered`.
