# CLAUDE.md

Guidance for Claude Code sessions working in this repository.

## What this project is

paper2code is a daily, unattended loop: pick one new arXiv paper, turn its main quantitative
claim into a small coding assignment with tests, have an AI builder implement it, and have an
AI inspector check the result for cheating. The record of every run is the product; the loop's
honesty is the top design priority. Read these before changing anything:

- `docs/superpowers/specs/2026-09-30-paper2code-design.md`: the binding design spec.
- `decisions.md`: the running journal of what happened and why, written for a future blog post.
  Append to it after every plan, build step, review, or notable bug. Plain language, the
  reasoning as well as the outcome, and call out "lesson worth a blog paragraph" moments.
- `docs/superpowers/plans/`: one implementation plan per build step, each with a Review Focus
  section and a self-review.
- `README.md`: current status and the local-mode commands.

## Where things are

```
src/paper2code/
  config.py            Config dataclass; config.yaml at the repo root is the live config
  cli.py               new-run, init-run, run (--until, --llm), fetch/score/select/scope/build/inspect/report
  manager/graph.py     LangGraph pipeline; RunContext; stage nodes skip-if-done and re-run after a crash
  manager/record.py    run.json (RunRecord), STAGES, create_run; atomic save
  manager/stages/      one module per stage
  manager/freeze.py    manifest hashing, freeze_scope, verify_manifest (anchored in run.json)
  manager/stubcheck.py every scoped test must fail on stubs *because of* the stubs
  manager/verdict.py   the mechanical outcome rule
  arxiv/               RSS daily feed, single-paper API lookup, HTML/PDF full text, polite client
  llm/                 ChatModel interface, OpenAI structured-output client, fake, cost accounting
  agents/scout, scoper prompts + pydantic schemas; agents/fake.py answers every role offline
  agents/builder       Builder protocol, stub builder (step 4 adds the Agent SDK builder)
  policies/            selection policies, plain functions over scorecard rows
  sandbox/runner.py    LocalTestRunner: pytest in an isolated subprocess over a snapshot
tests/                 pytest; tests/fixtures/canary is the EMA denoising canary assignment
```

Build order (spec section 16): 1 skeleton, 2 scout, 3 scoper, 4 builder (Agent SDK, Modal
sandbox and GPU test runner) are done; 5 inspector, 6 schedule and dashboard remain.

## How work is done here

- **Plan, execute, review.** Each build step gets a plan written with the writing-plans skill,
  executed task by task with the executing-plans skill on a feature branch, then reviewed by a
  fresh reviewer on the most capable model, with one fix pass. Native (inline) execution is the
  standing choice. Merge fast-forward into `master` and push both branches to
  https://github.com/aakarshan-coding/paper2code when the review is clean.
- **Test-first, always.** Write the failing test, watch it fail, implement, watch it pass, run the
  whole suite. A test that passes before its implementation exists is a finding about the test.
- **Resume safety is a standing review question.** A stage re-runs after a crash. Every stage must
  tolerate that: no duplicate records, no double billing, no half-written state frozen in.
- **Check causes, not symptoms.** The stub-check lesson: "the test failed" is not "the test failed
  because the thing it tests does not exist". Re-verify after acting when it is cheap.
- **Commit trailer:** every commit ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Running tests

```bash
pip install -e ".[dev]"
python -m pytest -q          # about two minutes; the stub-check and e2e tests spawn real pytest
PAPER2CODE_LIVE=1 python -m pytest tests/test_live_arxiv.py -q   # real arXiv requests, opt-in
```

In the Claude Code sandbox on this machine, pytest cannot create its default temp folder; set
`TEMP`, `TMP` and `TMPDIR` to the session scratchpad before running. Long shell heredoc batches
have failed to parse here more than once; for big test or file edits, write a small Python patch
script with the Write tool and run that.

## Money and secrets

- Scout and scoper calls go to OpenAI and cost real money (about 1 USD for a day's scoring, about
  0.35 USD per scoping attempt). `--llm fake` exercises the whole pipeline with no model.
  Prefer it unless the live result is the point.
- `OPENAI_API_KEY` lives in the Windows user environment, never in the repo or in chat.
- The builder (step 4) runs on the Claude Max subscription via `CLAUDE_CODE_OAUTH_TOKEN` from
  `claude setup-token`. `ANTHROPIC_API_KEY` must never be set in the builder's environment; it
  silently overrides the subscription token.
- Model-written code (scoped tests, builder output) is untrusted. The local runner scrubs
  credentials from its environment and runs in an isolated interpreter; without `--no-gpu` the
  agent works in a Modal sandbox (no secrets, network allowlist) and every test run executes in
  a Modal GPU function.
- Modal: the token is in `~/.modal.toml`. Run
  `python -m modal deploy src/paper2code/sandbox/modal_app.py` after changing the images or the
  GPU type (`PAPER2CODE_GPU`); GPU time is billed per `run_tests` call. On Windows set
  `PYTHONUTF8=1` for the deploy, or the CLI's progress output crashes on the console encoding.
  Importing `modal` on Windows switches asyncio to the selector loop, which cannot spawn
  subprocesses; the agent driver picks the proactor loop explicitly for that reason.
- Be polite to arXiv: the `PoliteClient` identifies itself, waits three seconds between requests,
  and backs off on 429/503. The export API throttles quickly; the RSS feeds do not.

## Writing for the person using this repo

Explanations are written for clarity and continuity. The reader is following a long project
across many sessions and wants to understand what happened and why, not decode a changelog.

Assume the reader understands the project technically, but does NOT necessarily remember every experiment ID,
implementation detail, previous debugging decision, or piece of terminology from earlier in the session. 


- Prefer complete, conversational technical explanations over compressed changelog prose. Say
  what changed, why it was needed, what it means for the next step, and what was decided along
  the way, in full sentences.
- After every change, give a brief plain-language recap. Brief means leaving things out, not
  compressing what is left into fragments.
- Lead with the outcome. Name files or functions only when the reader has to open them.
- Record decisions and their reasons in `decisions.md` as they happen, so the story stays
  continuous across sessions.
