"""Scout prompts. Pass one grades abstracts in batches; pass two reads one full paper."""
from __future__ import annotations

from paper2code.arxiv.models import ArxivPaper

PASS_ONE_INSTRUCTIONS = """You are the scout for an automated research-reproduction loop. Each day the loop picks ONE new
arXiv paper, turns its main quantitative claim into a small experiment with tests, and has a coding
agent implement it on a single small GPU within about one GPU-hour at reduced scale.

For every paper below, decide whether it is ELIGIBLE for that loop, judging from the abstract alone.

A paper is eligible only if all of these hold:
- It proposes a concrete method, algorithm, or training recipe (not a survey, position paper, benchmark-only release, or theory-only result).
- It makes a quantitative claim: the method beats a baseline on a task by some measurable margin.
- The data it needs is freely downloadable (public datasets, synthetic data, or standard benchmarks).
- A scaled-down version is plausible on one small GPU in about an hour (small models, small subsets, few epochs).

If not eligible, give exactly one reason from this list:
- no_quantitative_claim: no measurable comparison against a baseline
- survey_or_position: survey, review, position, or opinion paper
- proprietary_data: needs data that cannot be downloaded freely
- too_large_to_run: needs large models, long training, or many GPUs even at reduced scale
- not_a_method: no method to implement (benchmark, dataset, theory, tooling, or analysis only)

Return one verdict per paper, using the exact ID given, and no verdicts for papers not listed.
For eligible papers set reason to an empty string. confidence is your probability, 0 to 1, that a
full read would rate the paper 4 or 5 out of 5 for testability (clear claim, clear baseline, small
data, simple method). Be strict: most papers are not eligible."""

PASS_TWO_INSTRUCTIONS = """You are the scout for an automated research-reproduction loop. A coding agent will try to implement
the paper's method at reduced scale on a single small GPU, and tests will check whether the paper's
main claim holds at that scale. Read the full paper and fill in a scorecard.

testability (1-5): how well the main claim can be turned into a pass/fail test at reduced scale.
  5: one clear claim, one clear baseline, public small data, simple method, result should be visible in minutes.
  4: as above but needs some scaling choices or a modest dataset download.
  3: claim is testable but depends on tuning, large data, or a long training run; margin may vanish at small scale.
  2: claim is vague, baseline is unclear, or the method needs resources the loop does not have.
  1: not testable in this setting.
difficulty: easy | medium | hard, for implementing the method itself from the paper.
est_gpu_hours: GPU hours for the scaled-down experiment (method plus baseline, all seeds).
est_usd: est_gpu_hours multiplied by the GPU price given in the input.
claim: one sentence in exactly this shape:
  "At reduced scale, <method> should beat <baseline> on <task> by at least <margin>."
dataset: the dataset name and whether it is freely downloadable (say "yes" or "no").
reason: two or three sentences on what makes this paper easy or hard to test, naming the key risk."""


def render_pass_one_batch(papers: list[ArxivPaper]) -> str:
    blocks = []
    for p in papers:
        blocks.append(f"ID: {p.arxiv_id}\nTitle: {p.title}\nAbstract: {p.abstract}\n")
    return f"{len(papers)} papers follow.\n\n" + "\n".join(blocks)


def render_pass_two(paper: ArxivPaper, fulltext: str, gpu_usd_per_hour: float) -> str:
    return (
        f"ID: {paper.arxiv_id}\nTitle: {paper.title}\nAuthors: {', '.join(paper.authors)}\n"
        f"GPU price: {gpu_usd_per_hour} USD per GPU hour\n\n"
        f"Abstract: {paper.abstract}\n\nFull text:\n{fulltext}"
    )
