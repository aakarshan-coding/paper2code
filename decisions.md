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

---

## 2026-10-07 — Step 4a build: the first real builder run

Six tasks, test-first, then a fresh review. The suite went from 200 to 236 tests. For the first time a real Claude coding agent did the builder's job.

### What the agent actually did

Given the canary assignment (the EMA denoising paper from step 1), the locked-down agent saw exactly six tools in its session, read the spec and interface from its first prompt, wrote the module in one go, ran a quick sanity check of its own through the bash tool, and then pressed `run_tests` once. All seven public tests passed, the manager ended the session on the spot, and the inspector's hidden tests passed too. Outcome `completed`, one test run of the twenty-five allowed, about 1,300 tokens of subscription usage, sixteen seconds end to end. The agent's closing message summarised what it had built and noted it had used one run of twenty-five, which means it read the cap text in the result and understood it.

That happened twice. The first time was by accident: a step 2 test had used `--builder agent` as a stand-in for "not implemented yet", and once the builder existed, the unit suite quietly launched a real session. It passed, which was reassuring, but a unit suite must never spend subscription time on its own. The test now stubs the builder. The second run was the deliberate, opt-in live test, with the same result.

### The lockdown held

The session's advertised tool list was exactly the six builder tools and nothing else: no built-in file or shell tools, none of the account's connectors. That check runs before the first prompt and aborts the run if anything extra is visible; it is the single most important safety property of running an agent under a personal account, learned in the sibling project, and it is now verified on every build.

### Decisions recorded during execution

- The rate-limit handling landed one task early because the rewritten build session needed the exception type; a placeholder module held it until the real driver replaced the file.
- The rate-limit test observes the outcome one layer up (the run ends as `error/rate_limited`) rather than catching the exception, since the stage already handles it.
- The SDK's warning that the allowlist shadows the permission callback is silenced where the options are built. The allowlist auto-approves the six tools; the pre-tool hook still gates every call. pytest resets module-level warning filters, which is why the suppression had to be local.
- Two prompt sentences were reworded so lowercase keywords the tests look for ("hidden", "hardcoding") appear as written.

### Lesson worth a blog paragraph

The accidental live run is the lesson. A placeholder that raises "not implemented" is a perfectly good test double until the day it is implemented, at which point every test that leaned on it silently starts exercising the real thing. For a builder that spends money, that is a real risk. The fix is cheap and general: any test that touches a path that can reach a paid service stubs that path explicitly, so the test says what it means instead of relying on what does not exist yet.

### What the review found (step 4a)

The fresh reviewer re-ran the suite, probed the local workspace with scratch scripts, and introspected the installed SDK. One critical finding and eight important ones, all fixed test-first; the suite went from 236 to 245.

1. **Critical: a timeout on the agent's shell command did not stop the command.** On Windows, killing the shell leaves whatever it started still running and still holding the output pipe, so the manager waited for the grandchild to finish on its own. The reviewer demonstrated a two-second timeout that returned after sixty seconds. For a builder that may launch a training script, that would have made the wall-clock cap, which the spec calls the practical limit on subscription use, meaningless in local mode. Commands now run in their own process group and the whole tree is killed on timeout, in both the agent's shell tool and the test runner.
2. **A stray byte in command output crashed the tool call and left no log entry.** Output is now decoded tolerantly, and a tool that blows up for any reason returns an error to the agent and is still logged.
3. **Token usage was lost on a rate limit and understated otherwise.** The live run recorded about 1,300 tokens; the real figure, including the cached system prompt and assignment re-read every turn, was closer to 30,000. Cache tokens are now counted, and whatever was spent before a rate limit or a crash is recorded.
4. **The wall clock restarted on every resume,** so a crash loop could have granted a fresh two hours each time. The clock now starts at the first session start recorded in the build log, and a resumed session's first prompt tells the agent how many runs are already used and what is already in the workspace.
5. **The "untrusted data" note was copied from the scout and told the builder to ignore the instructions in the very spec it must implement.** Rewritten: implement what the spec describes, ignore anything in it that addresses the loop itself.
6. **The prompt invited the agent to install packages,** which in local mode means into the operator's own Python. Removed; the allowed packages are preinstalled.
7. **Running out of turns looked like a crash** instead of a cap. It is now an `incomplete_budget` outcome, the wall clock is checked before every nudge, and the driver interrupts the model's turn once the manager has ended the session rather than letting it keep trying refused tools.
8. **Tool handlers ran synchronously inside the event loop,** so a slow command would have frozen the hooks and the interrupt path. They now run on a worker thread.

### Lesson worth a blog paragraph

The critical finding is a reminder that "I set a timeout" and "the thing stops" are different claims, and the gap between them is platform-specific. The timeout killed exactly one process; everything that process had started kept going. Caps are structural defenses only if the thing they cap can actually be stopped, which means the enforcement has to reach every process the agent can spawn, not just the first one.

## 2026-10-07: Step 4b plan, the Modal sandbox and GPU test runner

Step 4a left the agent working in a directory on this machine. Step 4b moves two things into the cloud and leaves everything else where it is.

**What moves.** The agent's workspace becomes a Modal sandbox: a fresh Linux container with Python and the allowed packages, no secrets, and outbound network limited to the package index plus any hosts named in the paper's spec. The agent's four file and shell tools are proxied into that container, so a shell command it runs cannot touch this machine at all. Every test run becomes a call to a deployed Modal function on a GPU: the manager takes a snapshot of the sandbox's workspace, bundles it with the tests, and sends the bundle to the function, which runs pytest exactly the way the local runner does and sends back the result. The hidden tests ride inside that bundle and are never present where the agent works. The function's wall time is the GPU charge the budget cap counts.

**What stays.** The Agent SDK session, the subscription login, and the Modal token all stay on the manager's machine. The inspector's hidden-test run uses the same GPU function pointed at the hidden suite. `--no-gpu` keeps the whole step 4a local path.

**Decisions made while planning.**

- The test-running logic is written once as a pure function of (bundle bytes, timeout) and unit-tested locally; the Modal function is a two-line wrapper around it. The alternative, testing only through Modal, would have made the unit suite depend on the cloud.
- The sandbox is created at the start of the build stage and destroyed at the end, whatever the exit. The workspace is exported back into the run directory first, so a resumed run seeds its new sandbox from the previous attempt's work. A sandbox that dies mid-session is an infrastructure error (`sandbox_failed`), not the builder's failure.
- Bundles bigger than a configured size (50 MB) are refused with a message the agent can read, so a dataset or a virtual environment in the workspace cannot turn into a multi-gigabyte upload on every test run.
- The GPU type and the function timeout are fixed at deploy time from environment variables. Changing the GPU is a redeploy, not a code change.
- The builder's image installs the CPU build of PyTorch to stay small; the test image installs the default build so CUDA is available on the GPU.
- A live probe against the real account before planning confirmed every API call the plan relies on: sandbox creation in about 1.5 seconds, exec with a timeout, file reads and writes, the domain allowlist blocking a non-listed host, termination.

## 2026-10-07: Step 4b built, the builder in the cloud

Five tasks, test-first, then a fresh review. The suite went from 245 to 271 tests, plus three opt-in live tests against the real Modal account. For the first time the whole loop ran with the agent working somewhere other than this machine.

### What happened in the live runs

The deployed GPU function ran the canary's hidden suite in about nine seconds end to end, half a second of which was pytest; the rest was the container's cold start. The stub builder with remote tests reached `completed`. Then the real thing: a Claude agent session on this machine, its six tools proxied into a fresh Modal sandbox, wrote the canary module there, pressed `run_tests` once, the manager snapshotted the sandbox and sent the snapshot plus the public tests to the GPU function, all seven passed, the manager ended the session, exported the workspace back into the run directory, destroyed the sandbox, and the inspector's hidden run on the same GPU function passed too. Outcome `completed`, one test run, about seven GPU-seconds, twelve thousand tokens of subscription usage, under a minute of wall time. The hidden tests were never inside the sandbox.

### Decisions recorded during execution

- **The scope stage's stub check stays local.** As soon as `--no-gpu` became optional, the stub check (which had been using the same runner factory as the build) tried to reach the deployed function from inside the unit suite. It only needs tests to fail on stubs, never a GPU, and step 3 already decided it runs locally, so it now constructs the local runner directly. A run that stops before the build never touches Modal.
- **GPU seconds are counted from the manager's side of the call,** not from pytest's own clock inside the container. Modal bills the container for the whole call, including unpacking and start-up, so the manager-side time is the upper bound a budget cap should use. Under-counting is the failure that matters for a cap.
- **Shell commands inside the sandbox are relative to the working directory** (`find .`, `tar ... .`) rather than embedding the absolute root, and the exported tarball is re-prefixed in Python rather than with GNU tar's `--transform`. The real sandbox always runs in `/work`, so nothing changes in production, and the test double no longer depends on which tar is installed.
- **An agent failure is not a sandbox failure.** When the Agent SDK cannot start the CLI (that happens on this machine), the record now says `agent_failed`; `sandbox_failed` is reserved for the sandbox or the remote function breaking. Both are infrastructure outcomes, but a reader of the record should be pointed at the right side.
- Two step 2 tests that asserted `--no-gpu` was mandatory were deleted; the flag is optional by design now.

### Two Windows lessons worth a blog paragraph

The first live agent-in-sandbox run failed with "Failed to start Claude Code" and nothing else. The sandbox had been created, exported, and destroyed correctly; the Agent SDK simply could not launch the CLI. The cause: importing the Modal client on Windows switches asyncio to the "selector" event loop, and that loop cannot spawn subprocesses. Nothing in the step 4a tests had imported Modal, so the problem only appeared once both libraries were in the same process. The driver now chooses the "proactor" loop explicitly, whatever the policy says. The general lesson is that two libraries can each be correct and still break each other through global state, and the symptom will surface in whichever one runs second.

The second was smaller: the `modal deploy` command crashed on this machine because its progress output contains a character the Windows console encoding cannot represent, and Modal's side then reported the image build as "terminated due to external shut-down". Setting `PYTHONUTF8=1` fixed it. The image build itself, with CUDA PyTorch, took about a minute on Modal's side.

### Deferred

- The deploy is a manual step (`python -m modal deploy ...`). Step 6's scheduler should check the function exists before a run starts, so a forgotten deploy fails before any model spend rather than at the first `run_tests`.
- The sandbox's outbound allowlist is widened by every host that appears in a URL in `spec.md`. That is what the spec asks for (the dataset download), but it means a scoper that writes a stray link widens the allowlist. The inspector (step 5) should list the allowed hosts in its report.

### What the review found (step 4b)

The fresh reviewer read the whole branch, ran the suite, and wrote five probe scripts of its own. One critical finding, three important ones, ten minor. The critical one is the kind of thing that is obvious in hindsight and invisible while building.

1. **Critical: infrastructure failures were being handed to the model as tool errors.** The step 4a tool layer catches every exception from a tool and returns it to the agent as text, which is right for "file not found" and wrong for "the sandbox is gone" or "the GPU function is not deployed". On the real path, a dead sandbox or a forgotten deploy would have had the agent retrying `run_tests` for up to two hours of subscription time (the run counter never moved, so the 25-run cap never fired), and the record would have blamed the builder. Worse, if the public tests passed and the sandbox then died before the final export, the run directory would hold an empty workspace, the inspector would find it did not match the tree that passed, and the record would say `tests_tampered`: a false accusation produced by infrastructure, in a project whose stated top priority is the honesty of the record. The plan's own test for this scenario raised the exception from the wrong layer, which is why it passed. Three changes: a distinct infrastructure-error type that the tool layer turns into an ended session with reason `sandbox_failed`; every tree sent to the GPU function is first written into the run directory as a checkpoint, so the tree the inspector sees is the tree that was tested even if the sandbox vanishes a second later; and a failing terminate is logged rather than crashing the stage. The pinned tests now fail inside a tool call, the way the real agent would hit it, and one of them runs the inspector afterwards to prove the verdict is `completed`, not `tests_tampered`.
2. **Every fresh cloud session was told it was a resumed one.** The prompt lists what is already in the workspace so a resumed agent reads before rewriting; the assignment copy under `.assignment/` made that list non-empty on a brand-new sandbox. The live run completed anyway, but the prompt was lying to the agent on every run. The listing now ignores the assignment copy.
3. **The export had no size bound before the payload cap.** The cap was checked only after the whole archive had been downloaded to the manager, so a dataset in the workspace would have meant a multi-gigabyte download on every test run before the refusal. The size is now checked inside the sandbox and the download never happens.
4. **Workspace code can patch pytest from inside the test process** and forge the result. This is inherited from step 4a, already named in the README's known limitations, and the spec's answer is the step 5 inspector; the reviewer's probe is a ready-made adversarial canary for it.

The minors are in the ledger and the branch's final message; the reviewer's recommendations for step 6 (check the function is deployed before any model spend; warn when the pytest timeout exceeds the function's own) are noted there too. After the fix pass the suite stood at 278 tests, and the live agent-in-sandbox run on the fixed code completed again in 24 seconds with the checkpoint visible in the build log.

### Lesson worth a blog paragraph

A catch-all that turns every exception into a polite tool error is a kindness to the model and a trap for the operator. It made sense in step 4a, where the only things that could go wrong inside a tool were the agent's own mistakes. The moment the tool's other end became a remote service, the same catch-all started converting outages into "the builder kept failing". The fix is not to catch less but to classify: the agent's mistakes go back to the agent, the infrastructure's failures end the session. And the test that was supposed to guard this passed because it raised the failure one layer above where the real failure happens; a test is only as honest as the layer it exercises.

## 2026-10-07: Step 5 plan, the inspector

Steps 1 to 4 built a loop that can scope a paper, have an agent implement it in the cloud, and check mechanically that the scope was not touched and the hidden tests pass. What it cannot yet do is look at the code and say whether it is the paper's method or a trick that happens to pass. That judgment is step 5.

**What the inspector is.** One structured call to the inspector model (gpt-5.5 by default) over a text bundle the manager assembles: the paper, the assignment spec and interface, the public and hidden tests, every text file in the workspace with numbered lines, and a compacted build log. It answers with flags from the spec's fixed checklist (hardcoded result, test detection, sandbagged baseline, data leakage, wrong method), each with a file, a line and quoted evidence, plus a confidence that the code is the paper's method and a short plain-English summary. The schema itself limits the flag kinds, so the model cannot invent a category. The verdict rule does not change: any flag turns a passing run into `completed_suspicious`; the inspector never decides the outcome.

**What needs no model.** Two mechanical reviews run first and cost nothing: the build log is scanned for a builder that went looking for the hidden tests or the run record, and for test counts that shrank between runs; the workspace is scanned for code that imports pytest internals or probes the test environment. These are deterministic, so they can be canaries in their own right.

**A structural defense found by the step 4b review.** The reviewer showed that a workspace module can patch pytest's reporting at import time and make every test report "passed". The runner now registers a small witness hook before the workspace is importable; it records which tests actually raised, and the runner compares that with pytest's report. A test that raised but was reported passing is marked failed by the runner itself. The inspector's workspace scan flags the import as well, so this cheat is caught twice. Deeper patches of pytest's internals remain the inspector's concern; the lesson from step 4b stands that in-process execution is a trust boundary with a documented edge.

**Canaries.** The spec lists five adversarial canaries; they now live in one test module together with two new ones for the defenses above, each with a docstring naming what it guards. If a prompt change makes one stop firing, the suite goes red.

**Decisions made while planning.** The inspector gets no sandbox of its own because it executes nothing (a recorded deviation from the spec's section 10.1). A model outage during inspection ends the run as an infrastructure error and the stage re-runs cleanly; an unusable answer leaves the mechanical verdict in place with a note. The paper's text is now saved into the run directory by the scope stage so the inspector can read it; a hand-seeded run can pass it with `init-run --paper`. The unit suite never calls a model: every end-to-end test runs with the fake inspector, which flags the hardcoded canary by a simple pattern and is honest about being a heuristic.

## 2026-10-07: Step 5 built, the inspector

Six tasks, test-first. The suite went from 278 to 314 tests. The loop can now say not only "the tests passed" but "and here is whether the code is the paper's method, and what looked wrong".

### What the live run showed

The real inspector model (gpt-5.5) reviewed the two canary workspaces. For the honest reference implementation it returned no flags, said the code implements the EMA recurrence and the experiment as the paper describes, and put its confidence at 0.99. For the hardcoded cheat it returned two flags: `hardcoded_result` on the exact line that branches on the public seeds, and `wrong_method` on the line that returns fabricated numbers, with a summary explaining that the scaled experiment is missing entirely. Both reviews together cost under four cents and took ten seconds. That is a cheap judgment to add to every run.

### How the inspector is built

One structured call over a bundle the manager assembles: the paper, the spec, the interface, the public and hidden tests, every text file in the workspace with numbered lines, and a compacted build log, all capped so a large workspace cannot blow the prompt. The answer's schema limits flag kinds to the spec's five, so the model cannot invent a category, and each flag carries a file, a line and quoted evidence. The verdict rule did not change: any flag turns a passing run into `completed_suspicious`; a hidden failure or a tampered scope still outranks everything. The paper's text is now saved into the run directory by the scope stage, and a hand-seeded run can pass it with `init-run --paper`.

### What needs no model

Two mechanical reviews run before the inspector. The build log is scanned for a builder that went looking for the hidden tests or the run record, and for test counts that shrank between runs. The workspace is scanned for code that imports pytest internals or probes the test environment. They are deterministic, cost nothing, and have their own canaries.

### The runner's witness hook

The step 4b reviewer had shown that a workspace module can patch pytest's reporting at import time and make every test report "passed". The runner now registers a small witness hook before the workspace is importable. It records, for every test, whether the test actually raised. After the run, the runner compares that with pytest's own report; a test that raised but was reported passing is marked failed with a message saying so. The fixture that demonstrated the cheat is now a canary: through the whole pipeline it ends `incomplete_stuck` because the public run never passes, and the workspace scan flags the pytest import on top. The deployed GPU function was redeployed with the new bootstrap and catches it too.

### Decisions recorded during execution

- A model outage during inspection is handled by the pipeline node, not the stage: the run ends as `error` with reason `api_error`, no verdict is written, and the stage is left undone so that clearing the error and re-running repeats the inspection. The stage's own handling would have marked it done and made the error permanent. The scope stage keeps its step 2 behaviour.
- An unusable answer from the model (a refusal, a schema failure) leaves the mechanical verdict in place with a note that the review was unavailable; the hidden tests and the integrity checks still decide.
- A flag that points at a file not in the workspace is kept as given. The inspector may legitimately point at the build log, and a reader of the summary should see where it pointed.
- The unit suite never calls a model: every end-to-end test now passes `--llm fake`, and the fake inspector flags the hardcoded canary by a simple pattern while saying in its summary that it is a heuristic.
- The pytest-patching canary through the CLI ends `incomplete_stuck` rather than `hidden_failed` as the plan said, because the runner already fails the public run. The defense fires earlier than planned, which is better.
- Adding `init-run --paper` revealed that argparse had been accepting `--paper` as an abbreviation of `--paper-id`. The exact option now wins; worth remembering when adding options.

### Lesson worth a blog paragraph

The cheapest defenses are the ones that do not need a model. The witness hook is a dozen lines and catches the direct form of an entire class of cheat (rewrite the report) by construction rather than by judgment; obfuscated forms that reach the harness without naming it are the inspector's job, and the workspace scan now flags the usual ways of doing that. The build-log and workspace scans are regular expressions and an AST walk. The model review is the expensive, judgment-bearing layer on top, and its job is the cases no pattern can name: a baseline quietly weakened, a method that is almost but not quite the paper's. Put the structural defenses first, let the model handle the remainder, and give each layer a canary so a regression in any of them is a red test rather than a quiet loss.

### What the review found (step 5)

The fresh reviewer read the branch, ran the suite, and probed the new code with scratch scripts. No critical finding; five important ones, all fixed test-first, and the suite went from 314 to 323. Two of the five were honesty regressions of the kind this project exists to avoid.

1. **Deleting a scope file crashed the inspect stage instead of yielding `tests_tampered`.** Before this step the inspector never read the spec, so a missing file only showed up as a manifest mismatch. The new input bundle read `spec.md` unconditionally, and a builder that deleted it would have turned the loop's most important verdict into an infrastructure crash. A tampered scope now short-circuits: the verdict is written, nothing else runs, and the inspector is not billed.
2. **The documented recovery from a provider outage did not work through the pipeline.** The inspect stage was left undone, as intended, but the report stage then ran and advanced the record past it, so clearing the error and re-running did nothing. The test that was supposed to pin this only ran the single stage, never the pipeline. The report node now refuses to advance the record past a stage that ended in error, and the canary runs the whole pipeline twice.
3. **The probing patterns flagged ordinary machine-learning work.** A bare substring match on "hidden" meant that `python train.py --hidden 128` or `grep hidden_dim model.py` would have marked an honest run `completed_suspicious`, and the shrinking-count check fired on timeouts and syntax errors. The patterns are now path-shaped (`tests/hidden`, `../`, `run.json`), the count check ignores runs that timed out or failed to collect, and the false-positive cases are canaries too. On the other side, the build log was keeping only the first 120 characters of a command, so a probe at the end of a long command was invisible; whole commands are kept now.
4. **"Caught twice" and "structurally impossible" overstated the witness hook.** The reviewer pointed out, by reading rather than running (its exploit probe was stopped by a safety classifier), that workspace code could reach the bootstrap's state through the main module or reach pytest internals through indirect imports without ever writing the word. The witness state now lives in a closure, the scan flags the indirect routes (`__main__`, `importlib.import_module`, `__import__`, `atexit`, `sys.modules`), and the journal and the canary now say what is true: the direct form is caught by construction, the obfuscated forms are the inspector's job.
5. **The workspace scan did not skip virtual environments**, so a local-mode builder that created one would have had every site-packages pytest plugin flagged. The skip list is now shared with the bundle.

Rulings worth knowing: `cd ..` stays flagged, because in local mode the parent of the workspace is the run directory itself; a run whose inspector answer was unusable stays `completed` with a note rather than becoming a false accusation; a tampered scope no longer runs the hidden tests at all. The GPU function was redeployed with the new bootstrap and verified again on both canaries.

### Lesson worth a blog paragraph

Two of the five findings were about the record lying in opposite directions: one would have called an infrastructure crash what was really tampering, the other would have called honest work suspicious. Both came from code that was correct for the fixture it was written against and wrong for the world. The fix in each case was not cleverness but specificity: decide tampering before reading anything, and match the shape of a probe rather than a word that ordinary code uses all the time. And the resumability bug is a reminder that a test which exercises one layer proves that layer only; the claim in the journal was about the pipeline, so the test had to run the pipeline.

## 2026-10-07: Step 6 plan, the daily run

The last step makes the loop run on its own. One command, `paper2code daily`, does a whole day: it checks that everything needed is in place, creates today's run directory, runs the seven stages, pushes the run directory to a separate "runs" git repository after every stage, rebuilds a static dashboard over every run, and sends an optional notification. A Modal scheduled function runs that command once a day.

**Decisions made while planning.**

- **Preflight before spend.** The daily command refuses to start when a key is missing, the GPU function is not deployed, the `claude` program cannot be found, or the runs repository cannot be reached. A forgotten deploy then costs nothing. This was a reviewer suggestion from step 4b.
- **Publish after every stage.** The spec asks for it so a partial run is visible remotely. A failed push never fails the run: the commit stays local and the next push sends everything.
- **The dashboard is a static site inside the runs repository**, under `docs/`, so GitHub Pages can serve it with no extra hosting. It copies the run's files for drill-down, but never the hidden tests, because the site may be public. Every string is escaped; a paper title is untrusted text.
- **The cloud manager is simple because the Agent SDK's Linux wheel bundles the `claude` program.** Checked by downloading the wheel: it contains a 251 MB `claude` binary. So the Modal image only needs the Python package installed. No Node.
- **One Modal secret** holds the OpenAI key, the subscription token and the GitHub token. `ANTHROPIC_API_KEY` is removed from the environment before the manager starts, as the spec requires.
- **Two runs on the same day** get `-2`, `-3` suffixes, as the spec says. The second run usually ends `no_candidates` because the first one already graded the day's papers.
- **Things only the author can do:** create the runs repository on GitHub, create a push token, create the Modal secret, turn the schedule on, and approve the first real run. The plan stops before those.

## 2026-10-07: Step 6 built, the daily run

Five tasks, test-first, then a fresh review and one fix pass. The suite went from 323 to 357 tests. The loop now has a single command for a whole day, and a cloud function that can run it on a schedule.

### What `paper2code daily` does

First it runs preflight: a set of free checks. Is the OpenAI key present? Can the `claude` program be found? Is the GPU test function deployed on Modal? Do the two timeouts agree? Can the runs repository be reached? If any check fails, nothing is created and nothing is spent. Then it creates today's run directory (or `-2`, `-3` for a second run on the same day), runs the seven stages, and after every stage commits the run directory to the runs repository and pushes it. At the end it rebuilds the dashboard, a static website with one row per run and one page per run, pushes again, and sends an optional notification. A crashed run is still published and still shows on the dashboard. `daily --run DIR` resumes an existing run with the same publishing.

### What the cloud schedule is

A Modal function named `daily_run` that runs the same command inside a container once a day. The container has the package, the config file, and the `claude` program, which the Agent SDK's Linux build bundles (checked by opening the wheel: a 251 MB binary). Secrets come from one Modal secret. The function raises when the day did not finish cleanly, so Modal shows the call as failed. It is written and tested but not deployed: the author chose to keep the schedule off until a few runs look good.

### What was rehearsed

A full offline day on this machine: the real arXiv feed, the fake model, the stub builder, and a local git repository as the remote. The first run ended `completed`. A second run the same day became `2026-10-07-2` and ended `no_candidates`, because the first run had already graded the day's papers. The dashboard listed both. The first real paid run waits for the author's go-ahead and for the runs repository and token, which only the author can create.

### What the review found

One critical finding, seven important, nine minor. All critical and important ones were fixed test-first.

1. **The push wrote the GitHub token into the runs repository's own git config.** The push command used `-u`, which records the push URL as the branch's upstream, and that URL carried the token. In local mode the builder's shell runs inside that repository, so the token would have been one command away from an untrusted model. The fix removes the token from every URL: it now travels in an HTTP header passed on the git command line for clone, fetch and push only, and the push goes by remote name. A test publishes to a local remote with a token set and checks that the config file contains neither the token nor an upstream.
2. **A crash between clone and the config rewrite** could have left the token on disk forever. Moot now that no URL carries it, and the clean URL is re-applied on every start.
3. **Redaction ran after truncation**, so a token at the edge of a cut error message could survive. Order swapped.
4. **`daily --run DIR` was accepted and ignored**, and a run resumed the old way never reached the remote. Resume is now real, and the final publish stages the whole runs directory.
5. **Unattended failures were silent.** A preflight refusal or a crash sent no notification and the Modal call looked successful. Every end state now notifies with a status, and the cloud function fails loudly on a bad exit.
6. **The cron setting in the config file did nothing**; only an environment variable was read. The deploy now reads the config file, and environment variables override it.
7. **Falling back to a fresh local repository on any clone failure** (a ruling I made in Task 1) was wrong: in the cloud it would have meant a paid run whose pushes are all rejected and whose record vanishes with the container. The fallback is now only for an empty remote; an unreachable remote stops the day before anything is spent; and an existing plain directory adopts the remote's history so the first push fast-forwards.
8. **No journal entry yet.** This is it.

The minors are in the ledger and the branch's final message. Two are worth doing soon: the runs repository should get a `.gitignore`, and the dashboard should drop pages of runs that no longer exist.

### Lesson worth a blog paragraph

The critical finding came from a flag I added without thinking: `-u` on `git push`, the habit of every interactive push. It made git remember the URL, and the URL held a secret. The lesson is that any convenience flag on a command that handles a secret needs a reason, and the test that was supposed to guard the config file checked it at the wrong moment, before any push. The reviewer's suggestion, never put a secret in a URL at all, is the kind of invariant that is easy to test and hard to break by accident. The second lesson is about rulings: I had widened a fallback to make a test pass locally, and the reviewer traced what that widening would cost in the cloud. Rulings are cheap to make and the ledger made this one easy to find and reverse.

### Open items the author owns

- Create the runs repository on GitHub and a token with Contents: read and write; set `runs_repo_url` and `GITHUB_TOKEN`.
- Create the Modal secret `paper2code` from the shell and deploy `daily_run` when ready; `modal app stop paper2code` turns it off.
- Approve the first real paid run.
- Optionally enable GitHub Pages on the runs repository (branch `main`, folder `/docs`).
