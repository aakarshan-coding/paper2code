# paper2code: decisions and what happened

A running journal of the project, written to be read later, when the details have faded and the goal is to explain the whole thing clearly to someone else. Each entry says what happened, what was decided, why, what else was considered, and what went wrong. Newest entries at the bottom.

---

## 2026-09-30 — The idea and the design spec

### What the project is

A program that runs once a day, by itself. It picks one brand-new machine-learning paper from arXiv, turns the paper's main claim into a small coding assignment with tests, has an AI coding agent try to implement it, and then has a separate AI inspector check whether the result is honest. Every run leaves behind a folder with the full record.

### The key reframing

**The papers are not the product. The loop is.** Reproducing papers is a nice side effect, but the real question is: can an autonomous "scope it, then build it" system be trusted on a task its author never saw? Each day is one trial. The value is in the accumulated record: how often the loop finishes, where it stalls, and whether it can be caught cheating.

### Priorities, in order

1. **Honesty.** The builder must not be able to fake completion. Every defense is structural (enforced by what tools and files the agent is given), never by just asking it to behave.
2. **Good picks.** Choose papers that can actually be built within budget, and fall back gracefully when the choice was wrong.
3. **Observability.** Partial and failed runs are recorded as carefully as successful ones.

### The four roles, and why they never talk directly

- **Scout** reads abstracts, then full text for survivors, and grades testability.
- **Scoper** writes the assignment: a plain-language spec, the function signatures, public tests the builder sees, and hidden tests it never sees.
- **Builder** writes the code. It gets exactly two manager-owned buttons: "run the public tests" and "give up".
- **Inspector** re-checks everything, runs the hidden tests, and reviews the code against the paper.

All four communicate only through files in the run folder, written and read by a manager program. This is deliberate: if roles could message each other, one could coach another into cheating.

### Big decisions in the spec

| Decision | Why |
|---|---|
| **Hidden tests** the builder never sees | The main anti-cheating device. A builder that hardcodes answers for the public inputs fails on different seeds and settings. |
| **The manager decides when the build is done**, not the builder | A builder that could say "I'm finished" could lie. Instead the manager watches the test results and ends the session on the first all-pass. |
| **Freeze the assignment with file hashes** before building | If any test file changes afterward, the run is labeled tampered, overriding everything else. |
| **Mechanical verdict rule** | The inspector's judgment only produces flags. The final outcome is decided by a four-line rule, so no amount of persuasive prose from the builder changes the grade. |
| **Nine fixed outcomes** | `completed`, `completed_suspicious`, `hidden_failed`, `tests_tampered`, `incomplete_budget`, `incomplete_stuck`, `scope_rejected`, `no_candidates`, `error`. "Incomplete" is a normal result, not an error. This matters for the record: a loop that gives up honestly is a different story from one that crashes. |
| **LangGraph** for the pipeline | A graph with one node per stage and the state saved to disk after every node, so a crashed run resumes where it stopped. |
| **Modal** for cloud compute, with a per-run dollar cap | No local GPU. A spend ceiling on the account is the hard backstop. |
| **CPU-only sandbox for the builder; GPU only inside the test-run tool** | The builder spends most of its time reading and writing code. Putting the GPU only behind `run_tests` means the GPU bill measures exactly what the experiments cost. |
| **Full-text scoring capped at the top 10 papers per day** | The scout's expensive pass is the main cost lever; the cheap abstract pass filters hundreds down first. |

### The hybrid authentication decision

The author has a Claude Max subscription and prepaid OpenAI API credits, but no Anthropic API credits. Anthropic allows the Agent SDK to run unattended under a personal subscription via a one-year token from `claude setup-token`. So:

- The **builder** runs on the Claude Agent SDK under the subscription. It is the only role that benefits from a full coding harness, and it is one long session per day.
- The **scout, scoper and inspector** use OpenAI models on the prepaid credits. The scout processes hundreds of abstracts a day and must never eat into the subscription's rolling usage window.

Consequences worth remembering: `ANTHROPIC_API_KEY` must never be set in the builder's environment, because it silently overrides the subscription token. A subscription rate limit hit mid-build is recorded as an infrastructure outcome (`error` with reason `rate_limited`), not as the builder's failure, and the run is not retried that day.

### Naming

Started as "paperloop", renamed to **paper2code** the same evening.

### Build order chosen

Six steps, each usable on its own:

1. Run record, pipeline skeleton, local CLI, and a canary assignment that reaches `completed` with a stub builder.
2. Fetch, scout, select against live arXiv in dry-run mode.
3. Scoper, the stub check, feasibility check, freeze.
4. Builder in a Modal sandbox via the Agent SDK, with caps and rate-limit handling.
5. Inspector, verdict rule, adversarial canaries.
6. Daily schedule, dashboard, push of the run records to a separate repo.

---

## 2026-09-30 — Step 1 plan

### What step 1 is, in one sentence

Build the classroom, not the student: the folders, the grading rules, the test-running machinery, and a practice assignment with a known answer. Prove it all works by walking a fake student through it and seeing it graded `completed`.

### Decisions made while planning

- **One plan per build step.** Each step produces working software on its own, so each gets its own plan rather than one giant plan.
- **The canary assignment is an exponential-moving-average denoiser.** A fake paper claims that smoothing a noisy sine wave with EMA halves the error versus no smoothing. Chosen because it is pure Python, needs no GPU or download, is deterministic when seeded, and the claim holds with a wide margin. The margins were checked numerically before the plan was written, not assumed.
- **Two canary implementations.** A correct one, and a cheating one that returns good numbers only for the exact public inputs (seeds 0, 1, 2). The cheating one must pass public tests and fail hidden ones; it is the first adversarial test case.
- **Code lives under `src/paper2code/`** rather than at the repo root as the spec's tree sketch shows. Namespaced imports, same relative layout.
- **An `init-run` command** seeds a run from a hand-written assignment folder, standing in for the scope stage until step 3 builds it. Keep it afterward: it is how any hand-written assignment gets run.
- **The test runner is paranoid on purpose.** A skipped test, a suite that failed to even load, or a run that timed out all count as "not passed". Only a clean exit with at least one real passing test counts. A builder that hollows out a test with a skip marker should not be rewarded.
- **The pipeline state on disk is just `run.json`.** The LangGraph state carries only the folder path. Each stage checks the record first and skips itself if already done. The stage marker is advanced only after the work finishes, so a crash mid-stage re-runs that stage.
- **Native execution** (implementing in one session, one fresh reviewer at the end) was chosen over per-task subagents, because the tasks share tightly coupled interfaces that were already fully written in the plan, everything runs locally, and a mistake would be cheap to fix.

---

## 2026-09-30 — Step 1 build: what happened

Twelve tasks, each test-first: write the failing test, watch it fail, write the code, watch it pass, commit. Fifteen commits in all.

### Surprises during the build

- **The sandbox blocks pytest's temp folder.** Every test using a temporary directory failed with a permission error on the first run. The fix was environmental (point the temp variables at the session scratchpad), not a code change.
- **A real bug found by the tooling.** The task-closing script runs tests with standard input closed. Under that condition, every subprocess the test runner spawned failed on Windows with "the handle is invalid". The runner now explicitly gives the child process no stdin. This is the right behavior anyway: the production job is unattended and has no stdin.
- **A plan defect caught by a later task's tests.** The single-stage command refused to run `report` on a run that had ended early at `build` with an outcome, even though the full pipeline skips straight to `report` in that case. The guard was relaxed to allow a stage when an outcome is already set.
- **pytest tried to collect `TestRunResult` as a test class** because of its name. Marked as not-a-test.
- **The plan document itself was never committed** until the last task. Noticed and fixed.

### Deviation ledger (decisions made during execution, each recorded at the time)

- Feature branch in place instead of a separate worktree.
- `.superpowers/` (execution scratch) added to the ignore list.
- `__test__ = False` on the runner's dataclasses.
- `stdin=DEVNULL` in the runner.
- Single-stage guard relaxed when an outcome is set.

---

## 2026-09-30 — The final review, and what it found

A fresh reviewer with no memory of the build read the whole branch against the plan and spec, ran the tests, and actively tried to break the anti-cheating layer. This was the most valuable hour of the day.

### What it found

1. **Critical: the runner could be replaced by a one-file trick.** The runner started pytest with the workspace first on the import path. A cheating builder could drop a file named `pytest.py` into its workspace and that file would run *instead of* pytest, write a fake results file, and exit cleanly. The reviewer demonstrated this against the real hidden tests: verdict `completed` with zero real tests run. A `sitecustomize.py` in the workspace would run at interpreter start-up with the same effect.

   **Fix:** the runner now writes a tiny bootstrap script outside the workspace, runs Python in isolated mode (ignores the current directory and all `PYTHON*` environment variables), imports the real pytest *before* adding the workspace to the import path, and disables plugin auto-loading. A new test plants both attack files and asserts the real failing test is reported.

2. **Important: the fingerprint list could be regenerated.** The manifest of file hashes lives inside the folder it protects. A builder with write access could edit a hidden test and simply re-run the freeze. Also, deleting the manifest crashed the inspector instead of producing `tests_tampered`.

   **Fix:** at freeze time the manifest's own hash is stored in `run.json`, which the builder never receives. The inspector verifies the manifest against that anchor. Missing, unreadable, or re-generated manifests all end as `tests_tampered`.

3. **Important: the graded code might not be the code that passed.** After the public tests passed, nothing stopped the builder from writing more before the inspector looked.

   **Fix:** the runner returns a fingerprint of the exact snapshot it tested. On the public pass, that fingerprint is anchored in `run.json`. The inspector refuses a workspace whose fingerprint differs.

   **Decision recorded:** this drift is reported under `tests_tampered`, even though the spec defines that outcome for the assignment folder only. Reasoning: a graded tree that is not the tree that passed is the same class of altered evidence, and no other outcome name would be honest. This is a candidate amendment to the spec. The alternative, re-running the public suite at inspection time, would cost a second GPU run every day.

4. **Important: a crashing builder left no trace.** An exception propagated with nothing in the log or the run record.

   **Fix:** the error is appended to the build log and recorded in the run record's `error` field, without advancing the stage, so the stage re-runs on resume. A later success clears it.

5. **Important, deferred: in-process test patching.** A workspace module could, at import time, register a pytest plugin that rewrites every test report to "passed". No change to the runner closes this, because the tests run in the same process as the code under test. The structural answers belong to later steps: the step 3 stub check will record the expected set of test IDs at freeze time, and the step 5 inspector reviews the code for exactly this kind of trick. Documented as a known limitation in the README rather than fixed now.

### Lesson worth a blog paragraph

Three of the five findings were the same shape: **a defense that lived inside the territory it was defending.** The manifest inside the folder it protected. The import path that trusted the workspace before trusting pytest. The workspace graded after the builder had a chance to change it. The fix in every case was to move the anchor (a hash, an import order, a snapshot) to somewhere the builder cannot reach. "Structural, not instructional" turned out to also mean "outside the sandbox, not inside it".

### Minor findings, deferred with notes

Log-before-save ordering, a truncated results file raising instead of failing cleanly, child-process cleanup on timeout, CLI flags demanded by commands that do not use them, silent fallback on a missing config path, local-vs-UTC date for the run folder, no up-front validation of a hand-written assignment folder, and a couple of naming and documentation nits. All recorded; none affect honesty.

### Outcome

All five fixes came with tests that failed first. The suite went from 65 to 76 tests. Merged into master and pushed to https://github.com/aakarshan-coding/paper2code.

---

## Open questions carried forward

- Should the spec's `tests_tampered` definition be widened to cover post-pass workspace drift? (Decided yes in practice; spec text not yet amended.)
- Should the two anchor fields added to `run.json` be written into the spec's field list? (Same.)
- Step 2 is the first step that spends OpenAI credits. Model choice per role is deferred to implementation time by the spec; it needs a look at current pricing before the plan is written.

---

## 2026-10-04: A sibling project, designloop

While paper2code was paused after step 1, the author asked for an outreach-ready project for protein and AI-for-science startups, then asked for something more agent-shaped with a demo and metrics. The result is a separate repo at `../designloop`: an auditable benchmark of a tool-using design agent against baselines under an assay budget, on real deep mutational scanning data. Its full journal is in that repo's `decisions.md`.

Why it belongs in this journal: it reuses paper2code's central ideas, and it taught lessons that apply here.

- **The trust layer carried over.** A plan hashed before anything runs, metrics recomputed by the harness from a log, a pre-registered split, and an audit that catches edited or missing records. These are the same defenses the paper2code spec asks for, now exercised against an actual agent.
- **Running an agent under a personal subscription needs lockdown.** A nested Agent SDK session loaded the account's connectors (mail, calendar, drive) as available tools. The designloop driver switches off built-in tools, uses strict MCP configuration, and aborts at startup if the session advertises any tool outside its own allowlist. **The step 4 builder should do the same.**
- **The Windows launcher matters.** The SDK refuses `.cmd` shims and needs a native executable path.
- **OpenAI credits were exhausted on the day.** The paper2code spec's hybrid split (OpenAI for scout, scoper and inspector) needs those credits topped up before steps 2, 3 and 5.
