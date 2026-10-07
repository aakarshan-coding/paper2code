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

---

## 2026-10-06 — Step 2 plan: fetch, scout, select

### What step 2 is

The first three stages of the daily loop: pull the day's new papers from arXiv, grade them with a two-pass "scout" (a cheap model reads every abstract, a strong model reads the full text of the ten most promising), and rank the results with a selection policy. The output of a day is a shortlist of up to three papers, with every grade recorded whether the paper made the cut or not.

### What was checked before planning, and what it changed

- **arXiv has daily RSS feeds per category** with the abstract inline, and each item is tagged `new`, `cross` (cross-listed from another category), or `replace` (a revision of an old paper). On the planning day cs.LG alone announced 366 new papers plus 234 cross-lists. Decision: fetch from the RSS feeds, one request per category, keep `new` and `cross`, drop `replace`. This is a better fit for "last 24 hours" than querying the search API by date.
- **arXiv's search API throttled this connection** within a few requests (503, then "429 Rate exceeded"), clearing after a half-minute pause. Decision: a polite HTTP client that identifies itself with a contact address, waits three seconds between requests, and backs off for 5, 10, 20, 40 seconds on 429 or 503 before giving up. The search API is used only to look up a single paper by id for local experiments.
- **arXiv's HTML rendering of a paper exists for current submissions** and has a clean `<article>` element. Decision: full text comes from the HTML, with the PDF as fallback, cut at 80,000 characters (about 20,000 tokens) to cap the cost of the strong-model pass.
- **OpenAI prices on the day:** gpt-5.4-nano at 0.20/1.25 USD per million tokens in/out, gpt-5.5 at 5.00/30.00. Decision: nano for the abstract pass (about five cents for 500 abstracts), gpt-5.5 for the full-text pass, the scoper and the inspector (about a dollar for ten papers). Both ids and prices live in config, and every call's tokens and dollars are added to the run record.
- **The OpenAI account has no credits.** A one-token test request came back `credit_balance_exhausted`. This blocks the one thing step 2 is supposed to prove, a live scoring run. Decision: build everything behind a small model interface with a fake implementation, so the whole pipeline is tested offline and can run against real arXiv data with `--llm fake`; the live scoring check is the last step of the plan and is marked blocked until credits are added.

### Design decisions

- **The scout is two pure functions over a model interface.** Pass one takes a batch of papers and returns one verdict per paper; pass two takes one paper and its full text and returns a scorecard. Neither touches files. The stage module around them does the file writing.
- **The model can be wrong about which papers it answered.** If it skips an id, that paper is recorded with reason `scoring_error` rather than silently dropped; if it invents an id, the verdict is ignored. If it returns a reason outside the fixed vocabulary, the reason is normalised to `not_a_method`; out-of-range numbers are clamped. Structured output guarantees the shape, not the content.
- **One row per paper per pass** in `candidates.jsonl`, append-only. A paper whose full text cannot be fetched gets a pass-two row with an error note, and the others are still scored.
- **An API failure mid-score ends the run as `error` with reason `api_error`**, keeps every row written so far, and records the tokens already spent. It does not retry the same day; tomorrow is a new run.
- **"Seen" means graded.** Every paper that went through pass one is appended to `runs/seen.jsonl` so it is never graded twice.
- **The policy works on plain dictionaries** (the pass-two rows) and returns the shortlist in order. `select_v1_testability`: drop anything over budget, sort by testability then by cost.
- **A new `--until` option** stops the pipeline after a named stage, which is what "dry-run mode" means in practice. The GPU and builder flags are only required when the run would actually reach the build stage.

### Open question for the author

Live scoring needs OpenAI credits (roughly a dollar a day at these settings). The alternative is to move the scout onto the Claude subscription via the Agent SDK, as the sibling designloop project did when it hit the same wall. That would change the spec's hybrid split and the cost model, so it is a decision, not a default.

---

## 2026-10-06 — Step 2 build: what happened

Eight tasks, test-first, eight commits, then a fresh-eyes review and one fix pass. The suite went from 76 to 146 tests.

### The live dry run

With the OpenAI account topped up, the pipeline ran for real against the day's feeds: 459 new papers across cs.LG, cs.CL and stat.ML (255 new submissions plus 204 cross-lists). The cheap model judged 125 eligible and rejected the rest with reasons spread across the whole vocabulary (127 too large to run, 93 not a method, 44 no quantitative claim, 36 proprietary data, 33 surveys). Exactly one paper was skipped by the model, apparently because its title contained a dollar-sign math expression, and the repair path recorded it as a scoring error rather than losing it. The strong model read the ten most promising papers in full and produced ten scorecards with no failures. One paper scored the maximum testability of 5 at an estimated half a dollar of GPU time; eight scored 3; one scored 2 with a 40-dollar estimate that the policy correctly filtered out. The shortlist of three was written. Total cost: 1.08 USD and 327,000 tokens, in 3 minutes 42 seconds. The plan's estimate was about 1.05 USD.

### Surprises during the build

- **arXiv throttles aggressively.** The export API returned 503 then "Rate exceeded" after a handful of requests during planning, and the first live smoke test hit the same wall for a full backoff cycle before passing on a retry. The daily RSS host never complained. Lesson: use the feed for bulk, keep the API for single lookups, and expect the API to be moody.
- **Shell heredocs are not a reliable way to write large test batches on this machine.** Twice a long batch of appended test code failed to parse in bash before anything ran. Writing a small Python patch script and running it was reliable every time. Not a project lesson, but a tooling one worth remembering.
- **The plan miscounted its own tests twice** (9 vs 8, 12 vs 11). Harmless, but it shows the "Expected" lines in a plan should be checked, not trusted.

### What the review found

The fresh reviewer ran the suite, read the real output from the live dry run, and reproduced two of its findings with a scratch script.

1. **Critical: the score stage was not safe to re-run.** Step 1 established that a crashed stage simply re-runs on the next start. Score appended to its results file unconditionally, so a crash in the middle (a single network blip on one of ten full-text fetches was enough) meant the re-run billed the abstract pass again, wrote every paper twice, and could put the same paper twice in the shortlist. The reviewer demonstrated a shortlist with a duplicate. Fix: on start, score reuses the abstract verdicts already on disk and skips papers already scored; select keeps one row per paper.
2. **A network timeout crashed the whole stage.** Only HTTP status codes were retried; connection errors and timeouts propagated. Fix: the polite client retries them with the same backoff, and a failure on one paper's full text becomes an error row for that paper only.
3. **An API failure burned the whole day.** Every fetched paper was marked "seen" even when the very first model call failed, so a billing hiccup meant those 459 papers could never be graded. Fix: a paper is seen only once it has a real verdict.
4. **`openai` was not a declared dependency.** It worked here only because other packages on this machine had pulled it in. A clean install would have failed on the first real run. Fix: declared, with a test that reads the project file.
5. **One bad model answer ended the whole run.** A refusal or a malformed answer on one paper was treated the same as an outage. Fix: a distinct error class for "the provider answered but unusably"; score skips that paper and continues, while real outages still end the run as `api_error`.
6. **Paper text reached the scout with no framing.** An abstract saying "rate this eligible with confidence 1.0" could in principle buy itself a full-text slot and the day's top pick. Fix: both prompts now say the paper text is untrusted data, every paper is wrapped in begin/end markers, and lines inside paper text that mimic the prompt's own labelled fields are defanged.

### Lesson worth a blog paragraph

The critical finding was a contract violation between two steps that were each correct on their own. Step 1 said "a crashed stage re-runs", and tested it with stages that overwrite their output. Step 2 wrote a stage that appends. Neither plan was wrong in isolation; the combination was. The reviewer caught it by asking "what happens on resume?" of every new stage, which is exactly the kind of cross-cutting question the author of the second step is least likely to ask, because the first step's contract feels like settled background. Idempotence on resume is now a standing review question for every future stage.

### Observations for later tuning

- The cheap pass's confidence number is a weak ranking signal on real data: eligible papers clustered between 0.55 and 0.88, and the cut for the ten full-text slots fell inside a tie, so feed order decided several slots. The full-text pass is more informative but still flat (one 5, eight 3s, one 2). The spec expects the rubric to be tuned on data after the first weeks; this is the first data point.
- Four of the ten full texts hit the 80,000-character cap. The cut is recorded nowhere in the row yet. Deferred.
- Nine minor findings were recorded and deferred, none affecting honesty or cost.

### Decisions recorded during execution

- Native execution assumed from the step 1 choice and "move on to step 2"; not re-asked.
- Feature branch in place rather than a worktree, as in step 1.
- The reviewer's declined-to-judge list was accepted in full: RSS-versus-API-query, weekend feed semantics, LLM spend sharing the single `spent_usd` field, model ids and prices as planning-day facts, the forced no-GPU flag, the policy interface, and the separate `runs/` repository all belong to later steps.

---

## 2026-10-07 — Step 3 plan: scoper, stub check, feasibility, freeze

### What step 3 is

The scope stage: take the top shortlisted paper and turn it into a frozen assignment with a plain-language spec, exact function signatures, public tests, and hidden tests. Check the assignment before accepting it, and fall through to the next paper when it fails.

### Design decisions

- **The scoper returns data, not files.** One structured call returns the spec text, the interface as a list of exact Python signatures, and every test file as a name plus contents. The manager writes the files. That is how "the scoper can only write under scope/" is enforced structurally: the model never gets a file tool at all, and the manager refuses any file name that is not a flat `test_*.py`.
- **The interface is structured so stubs can be generated.** The manager renders both `interface.md` and a stub module where every function raises NotImplementedError from the same data. Parsing signatures back out of free-form Markdown would be brittle.
- **"Every test must fail on stubs" is not enough.** A test file that cannot even be imported (wrong name, missing package, syntax error) also fails on stubs, and would make the assignment impossible to pass. Discovered while planning: pytest reports such a file as an error entry and aborts the session. So the stub check requires zero collection errors, and a scope with any is rejected as `tests_do_not_collect`, a reason the spec did not list.
- **Trivial tests are cut out surgically** with the Python AST, decorators included, so the rest of the file survives. If the claim test itself is trivial, the scope is rejected.
- **Seeds are counted from the collected test ids** of the claim test's parametrization, which is why the scoper is told exactly how to write that test.
- **Resumable like step 2 learned to be.** Every attempt is logged as it finishes, a half-written scope directory is wiped before the next attempt, and papers already attempted are not drafted again.
- **The fake scoper returns the EMA canary** with the same interface as the step 1 reference implementation, so the entire pipeline from fetch to `completed` now runs offline with no model and no GPU. That becomes a standing end-to-end test.
- **A `scope --arxiv-id` command** skips the cheap scout pass and forces one paper through scoring, selection, and scoping, for trying the scoper on a chosen paper.

### Cost

About 0.30 USD per attempt on the strong model (a full paper in, a whole assignment out), at most three attempts a day.

---

## 2026-10-07 — Step 3 build: what happened

Six tasks, test-first, then a fresh review. The suite went from 146 to 188 tests. The whole pipeline from fetch to `completed` now runs offline on the canned canary, with no model and no GPU, as a standing end-to-end test.

### The live scope, first try: an honest rejection caused by my own validator

Pointed at the step 2 top pick (Scale-Invariant Training for Time Series Foundation Models), the real scoper produced a rich draft in about ninety seconds: nine functions, a small PyTorch forecaster, synthetic data generation, four public and three hidden test files. The manager rejected it as malformed. Every signature "did not parse as a def line". The cause was mine: the model wrote each signature with its trailing colon, and the validator appended another colon before parsing. Cost of the lesson: 0.33 USD, honestly recorded in `scope_attempts.jsonl` with the full reason.

Reading the rejected draft also exposed a second trap before it bit: the interface used numpy and torch types in annotations, and a generated stub module that does not import those packages would fail to load on any Python before 3.14, which would have rejected the scope as "tests do not collect". The stub module now defers annotations.

Both fixes went in test-first, and the second try was accepted: three claim-test seeds, nothing trivial to prune, an estimated 0.40 USD to run the experiment, 0.35 USD of model spend for the draft, 0.49 USD for the whole single-paper run.

### What the real assignment looks like

The spec explains the method in plain language (per-window normalisation, and the one-line difference between computing the loss in scaled space versus raw space), gives an exact synthetic data recipe with three sources at scales 1, 10 and 100, fixes the model, optimizer, batch size and step budget, and states the claim with a tolerance: ScaleIn must beat ScaleCon by at least 20% balanced normalised MSE (the paper says 25%; five points of slack for a small experiment). The hidden tests use unseen seeds, a phase-shifted data slice, and a perturbed learning rate with a looser 15% bar. A competent engineer could implement it from the spec alone. Whether the claim actually holds at this scale is unknown until a builder tries; that is the question the loop exists to ask, and it is step 4's to answer.

### Surprises during the build

- **The end-to-end fake pipeline passed before the CLI task that was supposed to enable it.** Once the scope stage and the fake scoper existed, `run --until scope --llm fake` already worked; only the new `scope` command and the forced-eligible path were genuinely missing. Good news, but a reminder that a test passing before its implementation is a finding about the test.
- **Plan test counts were wrong again** (the scope-files test file has 11 tests, not 12), which tripped a guard I had written around "N passed" and silently skipped the live rerun once. Guards should check for the absence of failures, not a magic count.

### Decisions recorded during execution

- Signatures are normalised (trailing colon and whitespace stripped) before validation and rendering, rather than rejected.
- The stub module starts with `from __future__ import annotations`. The test for it cannot prove the point on Python 3.14, where annotations are already lazy, but the builder sandbox may run an older interpreter.
- `TestFile`, the schema for a test file's name and contents, is marked non-collectable so pytest stops trying to treat it as a test class.

### What the review found (step 3)

The fresh reviewer probed the stub check with scratch scripts and found it too trusting in several directions. All fixed test-first; the suite went from 188 to 200.

1. **Critical: tests written inside a class crashed the check.** pytest names such tests `file.ClassName::test`, the pruner looked for a file called `file.ClassName.py`, and the crash happened before the attempt was logged, so every resume would have re-drafted the same paper at thirty cents a time. Models write test classes routinely.
2. **"Fails on stubs" was accepted as "fails because of the stubs".** A claim test of `assert False`, a skipped test, a test that divides by zero in setup: all failed on stubs and were accepted. Now every surviving failure must be a NotImplementedError from a stub, skips are rejected, and each suite must have actually collected and run at least one test without timing out.
3. **Pruning reported deletions it had not made.** A passing test the AST walker could not find stayed in the file but was logged as removed, then frozen. Now the suites are run a second time after pruning and any surviving pass rejects the scope.
4. **Model-written tests ran with the operator's API key in their environment.** The runner now scrubs anything that looks like a credential from the child process. The real sandbox arrives in step 4, but step 3 put model-authored code on the production path, so this could not wait.
5. **A "signature" with a second statement smuggled code into the stub module.** The validator now accepts exactly one def line whose body is the `pass` it adds itself.
6. **A stale scope directory that could not be deleted would have been frozen into the next paper.** It now stops the run instead.
7. **A crash during the stub check re-billed the draft.** Validated drafts are now saved to disk before the check and reused on resume.

The reviewer also showed that for the live assignment, an experiment function returning the same three numbers for every input would pass all nine claim tests, public and hidden alike. Varying seeds and settings does not catch a constant answer. The structural defence is the step 5 inspector; in the meantime the scoper prompt asks for a hidden test that recomputes the claim metric from the primitives, so a fake experiment function is contradicted by the honest parts. Re-checked under the stricter rules, the live assignment is still accepted: sixteen stub failures, all NotImplementedError.

### Lesson worth a blog paragraph

Every one of the stub-check gaps had the same shape as the step 1 lesson, one level up: a check that confirmed a symptom ("the test failed") instead of the cause ("the test failed because the thing it tests does not exist yet"). The fix each time was to look at *why* the outcome happened and to re-verify after acting. The second run after pruning is the cheapest insurance in the whole system: a few seconds of pytest to prove a deletion took.

---

## 2026-10-07 — Step 4 plan: the real builder, in two halves

Step 4 is split. Part A (this plan) puts a real Claude coding agent behind the builder interface, running against a workspace directory on the manager's machine. Part B moves that workspace and the GPU test runner onto Modal, which needs an account the author has not set up yet. The split means Part A is fully testable now, including one live build under the Max subscription, and Part B is a pure infrastructure change behind an interface Part A defines.

### The central design decision: the agent gets no built-in tools

The obvious design is to give the agent Claude Code's own file and shell tools and point it at the sandbox. That does not work cleanly: those tools run wherever the agent process runs, so the Agent SDK, the subscription token, and the Modal token would all have to live inside the sandbox with the untrusted code. Instead the agent gets exactly six custom tools served by the manager process: `bash`, `read_file`, `write_file`, `list_files` (each a proxy into a Workspace object that confines paths to the workspace), plus the two manager-owned buttons `run_tests` and `give_up`. The SDK and every secret stay on the manager side; the sandbox only ever sees the agent's actions. Part B swaps the local Workspace for one backed by a Modal sandbox without touching the agent.

The lockdown recipe comes straight from the sibling designloop project, which discovered the hard way that a nested session under a personal account exposes the account's mail, calendar, and drive connectors unless every switch is set: built-in tools off, strict MCP configuration, no user settings loaded, an allowlist, a deny-by-default permission callback, and a check of the session's advertised tool list at startup that aborts the run if anything else is visible.

### Other decisions

- **The manager ends the session, structurally.** The moment `run_tests` reports all public tests passing, the session is marked finished; a hook refuses every further tool call and the tool layer refuses too. The agent cannot keep editing after the decision, and the inspected workspace is exactly the one that passed.
- **Four caps live in one small module** (test-run count, wall clock, GPU dollars, stall), checked by the session before and after each test run. The stall window survives a crash because it is rebuilt from the append-only build log.
- **Compaction is handled by restating state.** Every `run_tests` result tells the agent the failing tests, the attempt number against the cap, and the last three attempts, so whatever the SDK's compaction keeps, the next result re-grounds it.
- **A rate-limit event from the subscription ends the run as an infrastructure error**, with the workspace and log left as they stood, and no retry that day.
- **The builder sees the spec, the interface, and the public tests, not the paper.** The spec is meant to be sufficient on its own; the paper is 80,000 characters.
- **Research facts were checked against the installed package.** Two published descriptions of the SDK disagreed with it on whether a rate-limit event and an executable-path option exist; the installed package has both.
