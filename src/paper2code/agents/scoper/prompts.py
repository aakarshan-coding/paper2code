"""Scoper prompt: one call that returns the whole assignment as data."""
from __future__ import annotations

from paper2code.agents.scout.prompts import BEGIN, END, UNTRUSTED_NOTE, _defang
from paper2code.arxiv.models import ArxivPaper

SCOPER_INSTRUCTIONS = """You are the scoper for an automated research-reproduction loop. You turn one paper into a small,
self-contained coding assignment with tests. A coding agent that has never seen the paper's code
will implement it from your spec and interface on one small GPU; a separate inspector will run your
hidden tests to catch cheating. Your assignment must be honest, runnable, and failing-until-done.

Return a ScopeDraft with these parts.

spec_md: Markdown. (1) The method in plain language, enough to implement it without the paper.
(2) The scaled-down experiment plan: dataset or synthetic data (with exact download or generation
instructions), model size, epochs or steps, the seeds, and the baseline. Size it to the budget given
in the input. (3) The claim: what the method must beat, on what metric, by what margin, with the
tolerance you consider fair at this scale.

interface: the module name (a valid Python identifier; the file is <module>.py) and every function
and class the tests import, as exact Python def lines with type hints, plus a one-line doc each.
Include a top-level function that runs the scaled experiment for one seed and returns the metrics
the claim test compares, so the claim test is a single call per seed.

public_tests: flat file names test_*.py. Unit tests for the method's components, and exactly one
file named test_claim.py whose claim test runs the scaled experiment and asserts the method beats
the baseline by at least the margin. The claim test MUST be parametrized over seeds with
@pytest.mark.parametrize("seed", [...]) using at least the minimum seed count given in the input.

hidden_tests: flat file names test_*.py, never shown to the builder. Variants of the claim test
with different seeds, a different data slice, and a perturbed hyperparameter, so a hardcoded answer
for the public seeds fails. At least one hidden test must RECOMPUTE the claim metric from the
interface's primitives (the data generator, the model, both the method's and the baseline's
loss or scoring functions, the single-condition training function) and cross-check it against
the numbers the experiment function reports, so an experiment function that returns constants
is contradicted by the honest parts of the implementation. Also list the seeds you used across
both groups in `seeds`.

Rules for every test file:
- Import only from the interface module, pytest, the standard library, and the allowed packages.
- Every test must FAIL against a stub module where every function raises NotImplementedError. A
  test that passes on stubs is removed automatically; if the claim test is trivial the whole scope
  is rejected. Do not write tests that only check types, imports, or constants.
- Tests must be deterministic given the seed and must run on CPU or a small GPU in minutes.
- Do not download anything inside a test except what spec_md says the dataset is.

est_gpu_hours and est_usd: your estimate for running the scaled experiment (method plus baseline,
all seeds, public and hidden) at the GPU price given. notes: anything the builder is likely to get
wrong, in two or three sentences.

""" + UNTRUSTED_NOTE


def render_scoper_input(
    paper: ArxivPaper,
    scorecard: dict,
    fulltext: str,
    budget_usd: float,
    min_seeds: int,
    allowed_packages: list[str],
    gpu_usd_per_hour: float,
) -> str:
    return (
        f"ID: {paper.arxiv_id}\n"
        f"Budget: {budget_usd} USD total for the scaled experiment\n"
        f"Minimum seeds for the claim test: {min_seeds}\n"
        f"Allowed packages: {', '.join(allowed_packages)}\n"
        f"GPU price: {gpu_usd_per_hour} USD per GPU hour\n"
        f"Scout claim: {_defang(str(scorecard.get('claim', '')))}\n"
        f"Scout dataset note: {_defang(str(scorecard.get('dataset', '')))}\n"
        f"Scout cost estimate: {scorecard.get('est_usd', '')} USD\n\n"
        f"{BEGIN}\nTitle: {_defang(paper.title)}\nAuthors: {_defang(', '.join(paper.authors))}\n"
        f"Abstract: {_defang(paper.abstract)}\n\nFull text:\n{_defang(fulltext)}\n{END}"
    )
