# Mentoring roadmap

The author is learning this codebase to own it. This file is the plan and the progress log. Each
session covers one subsystem from the real code, ends with questions and scenarios, and records
what was covered and what the author decided to change or investigate.

## Architecture in one page

paper2code is a pipeline of seven stages run by a manager, with four model roles that never talk to
each other. All state lives in files inside one run directory; the manager is the only writer of
the files that matter for honesty.

```
                 ┌──────────────────── manager (src/paper2code/manager) ────────────────────┐
                 │ graph.py: LangGraph, one node per stage, skip-if-done, resume after crash  │
 arXiv ──fetch──►│ record.py: run.json, the only state that matters                          │
                 │ stages/: fetch score select scope build inspect report                     │
                 │ freeze.py stubcheck.py caps.py verdict.py review.py: the honesty rules     │
                 │ daily.py preflight.py runs_repo.py: the unattended day and publishing      │
                 └───────┬──────────────┬───────────────┬──────────────────┬─────────────────┘
                         │              │               │                  │
                   agents/scout    agents/scoper   agents/builder     agents/inspector, writer
                   (OpenAI,        (OpenAI,        (Claude Agent SDK, (OpenAI, structured)
                    structured)     structured)     six MCP tools)
                                                        │
                                               sandbox/ (Modal: workspace, GPU test function,
                                                         session lifetime; local fallbacks)
                                                        │
                                               llm/ (ChatModel interface, OpenAI client, fake)
                                               arxiv/ (feeds, API, full text, polite client)
                                               dashboard/ (static site over the runs root)
```

Sizes (lines of Python, 2026-10-09): about 5,700 in `src/`, about 5,600 in `tests/`, 83 commits.

## Core execution flow of one day

1. `cli.py:main` parses `daily` and calls `manager/daily.py:run_daily`.
2. `preflight.py:run_preflight` checks keys, the CLI, the GPU function, timeouts and the runs repo. A failure returns before anything is created.
3. `runs_repo.py:RunsRepo.ensure` makes the runs root a git clone or init.
4. `record.py:create_run` makes `runs/<date>/run.json`.
5. `graph.py:run_pipeline` builds a LangGraph with `make_node` per stage; each node loads `run.json`, skips if the stage is done or an outcome is set, runs `stages/<stage>.py:run`, saves, advances `stage`, and calls `ctx.on_stage_done` (daily's publish hook).
6. Stages, in order:
   - `stages/fetch.py`: `arxiv/feed.py` reads the RSS feeds; `manager/seen.py` drops graded ids; writes `papers.jsonl`.
   - `stages/score.py`: `agents/scout/scout.py` runs pass one over abstracts (cheap model) and pass two over full text (`arxiv/fulltext.py`); writes `candidates.jsonl`; appends to `seen.jsonl`.
   - `stages/select.py`: `policies/` ranks scorecards; writes `selected.json`.
   - `stages/scope.py`: for each shortlisted paper, `agents/scoper/scoper.py:draft_scope` returns a `ScopeDraft`; `scope_files.py:write_scope` writes it; `stubcheck.py:run_stub_check` proves every test fails for the right reason; `freeze.py:freeze_scope` hashes and anchors it; `paper.md` is saved.
   - `stages/build.py:run_with_builder`: `BuildSession` owns `run_tests`, `give_up`, the caps (`caps.py`) and `build.log`. In Modal mode `sandbox/modal_session.py:modal_build_session` creates the sandbox, seeds it, and exports it; `agents/builder/agent.py:AgentBuilder.build` drives the Claude Agent SDK with the six tools in `agents/builder/tools.py`; test runs go through `sandbox/modal_runner.py:ModalTestRunner` to the deployed function in `sandbox/modal_app.py` whose body is `sandbox/remote_tests.py:execute_tests`.
   - `stages/inspect.py`: `freeze.py:verify_manifest` and the workspace hash; the hidden run; `review.py` mechanical flags; `agents/inspector` structured review; `verdict.py:decide`; `verdict.json`.
   - `stages/report.py`: `summary.md`; `manager/story.py:write_story` with `agents/writer`.
7. Back in `run_daily`: `dashboard/build.py:build_site`, a final publish of the whole runs root, and `notify.py`.

## Learning roadmap

The order follows dependencies and the project's priority: honesty first, then the parts that
spend money, then the parts that make it unattended.

| # | Subsystem | Why in this position | Files | Status |
|---|---|---|---|---|
| 1 | The run record and the pipeline | Everything else reads and writes through these. Resume semantics live here. | `manager/record.py`, `manager/graph.py`, `manager/outcomes.py`, `cli.py` | pending |
| 2 | Freezing, hashing and the verdict rule | The smallest and most important honesty code. | `manager/freeze.py`, `manager/verdict.py`, `stages/inspect.py` (integrity part) | pending |
| 3 | The test runner and the witness hook | Where untrusted code runs; process isolation, timeouts, JUnit, the cross-check. | `sandbox/runner.py`, `sandbox/remote_tests.py`, `sandbox/workspace.py` | pending |
| 4 | Scoping and the stub check | Where the assignment comes from and how trivial or broken tests are refused. | `agents/scoper/*`, `manager/scope_files.py`, `manager/stubcheck.py`, `stages/scope.py` | pending |
| 5 | The build session and caps | The manager's side of the builder: counting, caps, the build log, finish reasons. | `stages/build.py`, `manager/caps.py`, `manager/buildlog.py` | pending |
| 6 | The agent builder and its tools | The Claude Agent SDK session, lockdown, hooks, the six tools, rate limits. | `agents/builder/agent.py`, `tools.py`, `prompts.py`, `base.py`, `factory.py` | pending |
| 7 | The Modal sandbox and GPU function | Cloud isolation: workspace proxy, session lifetime, checkpoints, the deployed app. | `sandbox/modal_workspace.py`, `modal_session.py`, `modal_runner.py`, `modal_app.py` | pending |
| 8 | The inspector and mechanical reviews | Judgment over evidence: bundle, schema-limited flags, scans, the story's writer. | `agents/inspector/*`, `manager/review.py`, `manager/story.py`, `agents/writer/*` | pending |
| 9 | The scout, the selection policy and arXiv | Where the money goes first; structured outputs, retries, politeness. | `agents/scout/*`, `policies/*`, `arxiv/*`, `llm/*`, `stages/fetch.py`, `score.py`, `select.py` | pending |
| 10 | The unattended day | Preflight, publishing, the dashboard, the schedule, notifications. | `manager/daily.py`, `preflight.py`, `runs_repo.py`, `notify.py`, `dashboard/build.py` | pending |
| 11 | The test suite as a design document | Canaries, fakes, live tests, what is and is not covered. | `tests/test_adversarial_canaries.py`, `tests/conftest.py`, the fakes | pending |

After 11: the author writes their own technical vision (what to keep, change, remove, add) in
`docs/mentoring/vision.md`.

## Progress log

- 2026-10-09: roadmap written; no subsystem taught yet.
