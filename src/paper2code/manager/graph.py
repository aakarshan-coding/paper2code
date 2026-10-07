"""LangGraph pipeline: one node per stage, run.json on disk as the only state that matters."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, TypedDict

from langgraph.graph import END, START, StateGraph

from paper2code.config import Config
from paper2code.manager.record import STAGES, RunError, RunRecord, stage_index


class PipelineState(TypedDict):
    run_dir: str


@dataclass(frozen=True)
class RunContext:
    """Everything a stage needs besides the record. Bound into the graph at build time."""

    config: Config
    no_gpu: bool = True
    builder: str = "stub"
    reference_dir: Path | None = None
    llm: str = "openai"  # "openai" or "fake"
    until: str | None = None  # stop after this stage (dry run); None runs to the end
    http: Any = None  # test injection: a PoliteClient; None builds one from config
    chat_model: Any = None  # test injection: a ChatModel; None builds one from config
    force_eligible: bool = False  # local mode: skip scout pass one, treat every fetched paper as eligible


StageFn = Callable[[RunRecord, RunContext], None]


def default_stages() -> dict[str, StageFn]:
    from paper2code.manager.stages import build, fetch, inspect, report, scope, score, select

    return {
        "fetch": fetch.run,
        "score": score.run,
        "select": select.run,
        "scope": scope.run,
        "build": build.run,
        "inspect": inspect.run,
        "report": report.run,
    }


def _should_skip(record: RunRecord, stage: str) -> bool:
    if record.is_done(stage):
        return True
    return record.outcome is not None and stage != "report"


def make_node(stage: str, fn: StageFn, ctx: RunContext):
    def node(state: PipelineState) -> dict:
        record = RunRecord.load(Path(state["run_dir"]))
        if _should_skip(record, stage):
            return {}
        if ctx.until is not None and stage_index(stage) > stage_index(ctx.until):
            return {}
        if record.error is not None and record.error.reason == "exception":
            record.error = None  # a previous attempt crashed; this attempt starts clean
        try:
            fn(record, ctx)
        except Exception as exc:
            # Leave `stage` alone so the stage re-runs on resume, but record what happened.
            record.error = RunError(stage=stage, reason="exception", message=f"{type(exc).__name__}: {exc}")
            record.save()
            raise
        record.stage = stage
        record.save()
        return {}

    node.__name__ = f"{stage}_node"
    return node


def build_graph(ctx: RunContext, stages: Mapping[str, StageFn] | None = None):
    stages = dict(stages) if stages is not None else default_stages()
    graph = StateGraph(PipelineState)
    for stage in STAGES:
        graph.add_node(stage, make_node(stage, stages[stage], ctx))
    graph.add_edge(START, STAGES[0])
    for current, following in zip(STAGES, STAGES[1:]):
        graph.add_edge(current, following)
    graph.add_edge(STAGES[-1], END)
    return graph.compile()


def run_pipeline(run_dir: Path, ctx: RunContext, stages: Mapping[str, StageFn] | None = None) -> RunRecord:
    """Run from the run's current stage to the end. Safe to call again after a crash."""
    build_graph(ctx, stages).invoke({"run_dir": str(run_dir)})
    return RunRecord.load(run_dir)


def run_stage(stage: str, run_dir: Path, ctx: RunContext, stages: Mapping[str, StageFn] | None = None) -> RunRecord:
    """Run exactly one stage.

    Refuses if the previous stage has not completed, unless the run already has an
    outcome: then the pipeline itself would skip straight to report, so single-stage
    invocation may too.
    """
    stages = dict(stages) if stages is not None else default_stages()
    record = RunRecord.load(run_dir)
    idx = stage_index(stage)
    if idx > 0 and record.outcome is None and not record.is_done(STAGES[idx - 1]):
        raise ValueError(
            f"{stage} requires {STAGES[idx - 1]} to have completed; run.json says stage={record.stage!r}"
        )
    make_node(stage, stages[stage], ctx)({"run_dir": str(run_dir)})
    return RunRecord.load(run_dir)
