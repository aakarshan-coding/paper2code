# How a run is kept honest

paper2code's builder is an AI coding agent. It reads the assignment and the public tests, writes
code, and asks for test runs. An agent that wants to pass can cheat: hardcode the answers the
public tests expect, edit the tests, patch the test runner, or detect when it is under test. The
record of a run is only worth something if those routes are closed.

This page lists the defenses in the order a run meets them. Each one names the code that
implements it and the test that pins it. The design rule throughout: defenses are structural
where possible. They do not ask the agent to behave; they make misbehaviour fail or show.

## 1. The scope is frozen and anchored

When the scoper's assignment is accepted, every file under `scope/` is hashed into
`manifest.json`, and the hash of the manifest itself is written into `run.json`, which the builder
never receives. The inspector re-hashes the scope and compares. Any difference, including a
re-frozen manifest, ends the run as `tests_tampered`, which outranks every other outcome.

- Code: `src/paper2code/manager/freeze.py` (`freeze_scope`, `verify_manifest`), `src/paper2code/manager/verdict.py` (`decide`)
- Tests: `tests/test_freeze.py` (`test_anchor_detects_rewritten_manifest`, `test_modified_file_detected`), `tests/test_inspect_stage.py` (`test_edited_hidden_test_is_tests_tampered`, `test_refrozen_scope_is_tests_tampered`, `test_deleted_manifest_is_tests_tampered`), `tests/test_adversarial_canaries.py` (`test_canary_modified_test_file_is_tests_tampered`)

The passing workspace is anchored the same way: the hash of the tree that passed the public tests
is stored, and a workspace that changed afterwards is `tests_tampered` too
(`test_workspace_changed_after_public_pass_is_tests_tampered`).

## 2. Hidden tests exist and the builder never sees them

The scoper writes two test sets. The public set is given to the builder. The hidden set uses other
seeds, sizes and parameters, and is run once by the inspector. A workspace that fits the public
tests and fails the hidden ones ends as `hidden_failed`.

- Code: `src/paper2code/agents/scoper/schemas.py` (`ScopeDraft.hidden_tests`), `src/paper2code/manager/stages/inspect.py`
- Tests: `tests/test_inspect_stage.py` (`test_hardcoded_workspace_is_hidden_failed`), `tests/test_adversarial_canaries.py` (`test_canary_public_pass_hidden_fail_is_hidden_failed`)
- Fixture: `tests/fixtures/canary/hardcoded/` is an implementation that recognises the public seeds and returns the expected numbers.

## 3. The stub check proves every test fails for the right reason

Before a scope is frozen, every test is run against stubs that only raise `NotImplementedError`. A
test that passes on stubs is trivial and is removed; a test that fails for any other reason (an
import error, a missing package, a timeout) rejects the scope; a claim test that is not
parametrised over enough seeds rejects it too. "The test failed" is not "the test failed because
the thing it tests does not exist".

- Code: `src/paper2code/manager/stubcheck.py`
- Tests: `tests/test_stubcheck.py`, `tests/test_adversarial_canaries.py` (`test_canary_planted_trivial_test_is_removed_by_the_stub_check`)

## 4. The builder works in a sandbox with nothing to steal

The agent's four file and shell tools are proxied into a Modal sandbox: a fresh container with
Python and the allowed packages, no secrets, and outbound network limited to the package index and
hosts named in the spec. Paths are confined to the workspace. The sandbox is destroyed when the
build ends, after its workspace is exported into the record. Every tree sent for testing is
checkpointed into the record first, so a sandbox that dies cannot leave the inspector looking at
the wrong tree.

- Code: `src/paper2code/sandbox/modal_workspace.py`, `src/paper2code/sandbox/modal_session.py`
- Tests: `tests/test_modal_workspace.py` (`test_modal_workspace_confines_paths`), `tests/test_modal_session.py` (`test_passing_tree_is_checkpointed_so_a_failed_export_cannot_look_like_tampering`, `test_dead_sandbox_during_a_tool_call_ends_the_run_as_sandbox_failed`)

In local mode (`--no-gpu`) the shell runs on the operator's machine with credentials scrubbed from
its environment; that mode is for trusted assignments and prompt iteration only.

## 5. The manager owns the test runs

The agent cannot run the tests itself. It calls a `run_tests` tool; the manager snapshots the
workspace, bundles it with the public tests, and sends the bundle to a separate GPU function. The
hidden tests travel in the inspector's bundle only. The manager counts runs, enforces the caps
(test runs, wall clock, GPU dollars), and ends the session on the first full pass. Infrastructure
failures end the run as `error`; they are never handed to the agent as something to retry.

- Code: `src/paper2code/manager/stages/build.py` (`BuildSession`), `src/paper2code/manager/caps.py`, `src/paper2code/sandbox/modal_runner.py`, `src/paper2code/agents/builder/tools.py`
- Tests: `tests/test_build_stage.py`, `tests/test_caps.py`, `tests/test_modal_session.py` (`test_remote_failure_during_run_tests_ends_the_run_as_sandbox_failed`)

## 6. A witness hook inside the test runner

Workspace code runs in the same interpreter as pytest, so it can patch pytest's reporting at import
time and make every failure report as a pass. Before the workspace is importable, the runner
registers a small plugin that records whether each test actually raised. After the run, the runner
compares that record with pytest's report; a test that raised but was reported passing is marked
failed. The witness state lives in a closure, so workspace code that reaches the bootstrap module
finds nothing to swap.

- Code: `src/paper2code/sandbox/runner.py` (`_BOOTSTRAP`, `cross_check`)
- Tests: `tests/test_runner_crosscheck.py` (`test_local_runner_catches_in_process_report_tampering`, `test_cross_check_survives_a_workspace_that_reaches_the_bootstrap_module`, `test_cross_check_ignores_expected_exceptions`), `tests/test_adversarial_canaries.py` (`test_canary_pytest_patching_workspace_is_caught_twice`)
- Fixture: `tests/fixtures/canary/pytest_patched/` is the cheat.

This catches the direct form. Obfuscated access to pytest internals is the inspector's job, and
the workspace scan below flags the usual routes.

## 7. Mechanical scans that need no model

Two deterministic reviews run before the inspector. The build log is scanned for a builder that
went looking for the hidden tests or the run record (path-shaped patterns, so `--hidden 128` is
not probing) and for test counts that shrank between runs. The workspace is scanned for code that
imports pytest internals or probes the test environment (`PYTEST_CURRENT_TEST`, frame inspection,
`sys.modules`, `__main__`, `importlib.import_module`, `__import__`, `atexit`).

- Code: `src/paper2code/manager/review.py`
- Tests: `tests/test_review.py`, `tests/test_adversarial_canaries.py` (`test_canary_hidden_test_probing_in_build_log_is_flagged`, `test_canary_test_count_decrease_is_flagged`)

## 8. The inspector reviews the code against the paper

One structured call reads the paper, the assignment, the tests, the workspace with numbered lines,
and the build log, and returns flags. The schema limits flag kinds to five: `hardcoded_result`,
`test_detection`, `sandbagged_baseline`, `data_leakage`, `wrong_method`. Each flag carries a file,
a line and quoted evidence, plus a confidence that the code is the paper's method. Everything the
inspector reads is framed as untrusted data.

- Code: `src/paper2code/agents/inspector/`
- Tests: `tests/test_inspector.py`, `tests/test_inspect_stage.py` (`test_hardcoded_workspace_gets_a_hardcoded_result_flag`, `test_bad_inspector_output_keeps_the_mechanical_verdict`), `tests/test_adversarial_canaries.py` (`test_canary_hardcoded_answers_get_hardcoded_result_flag`)
- Live: `tests/test_live_inspector.py` (opt-in) runs the real model on the canaries; on the first run it flagged the hardcoded cheat on the exact line and cleared the honest implementation with confidence 0.99.

## 9. The verdict rule is mechanical

```
if hashes mismatch:      tests_tampered
elif hidden tests fail:  hidden_failed
elif no flags:           completed
else:                    completed_suspicious
```

The inspector's judgment affects flags only. It cannot declare a run complete, and a flag is a
reason to look, not a proof. If the inspector model is unavailable, the mechanical checks still
decide and the summary says the review was missing.

- Code: `src/paper2code/manager/verdict.py`
- Tests: `tests/test_verdict.py`

## 10. The story is prose over facts

The published write-up of a run is written by a model, but the model may only point: it returns
prose and code excerpts as line ranges. The manager prints every number, table, test result and
code line from the record, reads excerpts from the real files, drops any excerpt outside the
workspace or outside `.py` files, never shows the writer the hidden tests, and redacts any prose
line that quotes one.

- Code: `src/paper2code/manager/story.py`, `src/paper2code/agents/writer/`
- Tests: `tests/test_story.py` (`test_excerpts_cannot_reach_outside_the_workspace_on_any_platform`, `test_hidden_test_code_never_reaches_the_story`, `test_style_check_applies_to_prose_only_and_leaves_code_blocks_alone`)

## 11. Canaries keep all of this alive

`tests/test_adversarial_canaries.py` holds one test per defense, each with a docstring naming what
it guards. A prompt change, a runner change or a review change that makes one stop firing turns the
suite red. The spec lists five of these; the suite has grown to twelve.

## What is not defended

- Obfuscated patches of pytest's runner internals inside workspace code are detectable only by the inspector.
- The inspector's judgment is a model's judgment. Flags are visible in the record with their evidence; readers decide.
- The model providers themselves are trusted to run the models they say they run.
- In local mode the agent's shell is on the operator's machine. The sandbox is the real boundary.

Every one of these is written down in `decisions.md`, with the review that found it.
