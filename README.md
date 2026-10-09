# paper2code

[![tests](https://github.com/aakarshan-coding/paper2code/actions/workflows/tests.yml/badge.svg)](https://github.com/aakarshan-coding/paper2code/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![runs dashboard](https://img.shields.io/badge/runs-dashboard-green.svg)](https://aakarshan-coding.github.io/paper2code-runs/)

An unattended daily loop that picks a new arXiv paper, turns its main claim into a small tested
coding assignment, has an AI coding agent implement it in a cloud sandbox, and has a second model
check the result for cheating. Every run is published as a record: the paper, the assignment, the
agent's code, every action it took, the test results, the verdict, and a written story.

The hard part is not building the code. It is keeping the record honest when the builder is an
agent that can read the tests it must pass. Most of this repository is the set of defenses that
make a passing run mean something.

**See it:** the [runs dashboard](https://aakarshan-coding.github.io/paper2code-runs/) and the
[story of the first real run](https://aakarshan-coding.github.io/paper2code-runs/runs/2026-10-08/index.html),
a BF16 weight compressor from a paper published the same day, built and inspected in nine minutes
for about two dollars.

## How a day works

```
 arXiv feeds ─► fetch ─► score ─► select ─► scope ─► build ─► inspect ─► report
                         scout     policy   scoper   builder   inspector
                        (OpenAI)           (OpenAI) (Claude    (OpenAI)
                                                     agent in
                                                     a Modal
                                                     sandbox)
```

| Stage | What happens | Who |
|---|---|---|
| fetch | Today's submissions in the configured categories; papers already graded are skipped | code |
| score | Two passes: a cheap classifier over abstracts, a strong model over the survivors' full text | scout model |
| select | A pluggable policy ranks the scorecards; the top pick goes forward, the rest are fallbacks | code |
| scope | The paper's claim becomes a spec, an interface, public tests and hidden tests; a stub check proves every test fails for the right reason; the scope is frozen and hashed | scoper model |
| build | A Claude coding agent works in a CPU sandbox with six tools; tests run on a GPU function it cannot reach; caps on runs, time and dollars | Agent SDK |
| inspect | Hidden tests, integrity checks, mechanical scans, and a code review against the paper; a fixed rule turns these into the outcome | inspector model |
| report | Summary, verdict, the run's story, the dashboard, a push to the runs repository | code, writer model |

Outcomes are a fixed list: `completed`, `completed_suspicious`, `hidden_failed`, `tests_tampered`,
`incomplete_budget`, `incomplete_stuck`, `scope_rejected`, `no_candidates`, `error`.

## How a run is kept honest

The builder is untrusted. So is every file a model writes, including the tests. The defenses are
layered and mostly structural: they do not ask the agent to behave, they make misbehaviour fail.

| Defense | What it stops |
|---|---|
| Frozen scope with hashes anchored in the run record | Editing a test, re-freezing, or swapping the passing workspace |
| Hidden tests the builder never receives | Fitting to the visible tests |
| Stub check at scoping time | Trivial tests that pass without an implementation |
| Sandbox with no secrets and a network allowlist | Reaching the manager, the record, or the internet at large |
| Manager-owned test runs on a separate GPU function | Running or altering the tests from inside the sandbox |
| A witness hook inside the test runner | Patching pytest's reporting so failures report as passes |
| Mechanical scans of the build log and the workspace | Probing for hidden tests, shrinking test counts, test-environment detection |
| An inspector review with schema-limited flags | Hardcoded results, weak baselines, data leakage, a wrong method |
| A mechanical verdict rule | The inspector can only add flags; it cannot declare a run complete |
| Adversarial canaries in the test suite | A prompt or runner change that silently weakens any of the above |

Each row is pinned by tests. [docs/honesty.md](docs/honesty.md) walks through them in order with
links to the code and the canaries.

## Try it in two minutes, no keys

```bash
git clone https://github.com/aakarshan-coding/paper2code && cd paper2code
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
paper2code daily --llm fake --no-gpu --builder stub --reference tests/fixtures/canary/reference --no-publish
```

That runs the whole loop offline on a built-in canary paper: a fake model answers every role, a
stub builder copies a known-good implementation, tests run locally, and a run directory with a
dashboard appears under `runs/`. Replace `reference` with `hardcoded` to watch the hidden tests
and the inspector catch a cheat.

```bash
python -m pytest -q          # 377 tests, about three minutes; no model, no network
```

## Run it for real

You need an OpenAI key, a Claude subscription token (`claude setup-token`), a Modal account, and
optionally a GitHub repository for the runs. Costs per day: about 2 USD of OpenAI tokens, cents of
GPU time, and some minutes of the Claude subscription.

```bash
python -m modal setup                                             # once
PYTHONUTF8=1 python -m modal deploy src/paper2code/sandbox/modal_app.py   # once, and after image changes
paper2code preflight                                              # free: keys, CLI, GPU function, repo
paper2code daily                                                  # one unattended day
```

`preflight` refuses to start the day when anything is missing, so a broken setup costs nothing.
`daily` pushes the run directory to the runs repository after every stage (set `runs_repo_url` in
`config.yaml` and `GITHUB_TOKEN`), rebuilds the dashboard, and can post a JSON notification.
A Modal scheduled function (`daily_run`) runs the same command every day once deployed.

Single stages and single papers work too:

```bash
paper2code scope --arxiv-id 2610.09916          # score, select and scope one paper
paper2code build --run runs/<date> --no-gpu     # the agent on this machine, no sandbox
paper2code story --run runs/<date>              # rewrite the run's story
```

See [CLAUDE.md](CLAUDE.md) for every command and the operating notes.

## What is in a run

```
runs/2026-10-08/
  paper.md             the paper's text
  scope/               spec.md, interface.md, tests/public, tests/hidden, manifest.json (frozen)
  workspace/           the agent's code, as it stood when the build ended
  build.log            every tool call, test run and cap event, written only by the manager
  verdict.json         hidden test results, integrity checks, flags with file and line, confidence
  story.md             the run as a blog post: prose from a writer model, every fact from the record
  summary.md, run.json
```

`story.md` is worth a look: the writer model may only point (a line range, a judgment) while the
manager prints (the lines, the numbers), so the prose reads like a person wrote it and every
checkable claim traces to a file.

## Design and history

- [docs/superpowers/specs/2026-09-30-paper2code-design.md](docs/superpowers/specs/2026-09-30-paper2code-design.md): the design, written before any code.
- [docs/honesty.md](docs/honesty.md): the defenses, in order, with the tests that pin them.
- [decisions.md](decisions.md): the journal. Every step was planned, built test-first, reviewed by a fresh model, and fixed; the journal records what each review found and what was learned.
- [docs/superpowers/plans/](docs/superpowers/plans/): one implementation plan per build step.

Built with Claude Code over six planned steps, each with an independent review. The reviews found
real problems: a timeout that did not stop child processes, a token written into a git config, an
infrastructure error swallowed by a tool layer, a path check that missed Windows paths. All of them
are in the journal with the fix and the test that now guards it.

## Status

All six build steps are complete and one real run has been published. The cloud schedule is
written and tested but not switched on; runs are started by hand for now.

## License

MIT. See [LICENSE](LICENSE).
