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
