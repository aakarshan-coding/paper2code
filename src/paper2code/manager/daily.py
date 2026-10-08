"""One unattended day: preflight, create the run, run the pipeline publishing after every stage,
rebuild the dashboard, publish, notify. Nothing here changes what a stage does."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from paper2code.config import Config
from paper2code.dashboard.build import build_site
from paper2code.manager.graph import RunContext, run_pipeline
from paper2code.manager.notify import notify
from paper2code.manager.preflight import Check, all_ok, format_checks, run_preflight
from paper2code.manager.record import Caps, RunRecord, create_run
from paper2code.manager.runs_repo import GitError, RunsRepo
from paper2code.manager.seen import SEEN_FILE
from paper2code.manager.verdict import VERDICT_JSON, Verdict


@dataclass
class DailyResult:
    run_dir: Path | None = None
    record: RunRecord | None = None
    checks: list[Check] = field(default_factory=list)
    published: bool = False
    publish_error: str = ""
    site: Path | None = None
    notified: bool = False


def _caps(config: Config) -> Caps:
    return Caps(test_runs=config.caps.test_runs, wall_clock_s=config.caps.wall_clock_s, stall_n=config.caps.stall_n)


def _payload(record: RunRecord | None, status: str, message: str = "") -> dict:
    """What the notification carries. `status` is finished | crashed | preflight_failed | repo_unavailable."""
    if record is None:
        return {"status": status, "message": message, "run_id": None, "outcome": None}
    flags = 0
    if (record.run_dir / VERDICT_JSON).exists():
        try:
            flags = len(Verdict.load(record.run_dir).flags)
        except Exception:
            flags = -1
    return {
        "status": status,
        "message": message,
        "run_id": record.run_id,
        "outcome": record.outcome.value if record.outcome else None,
        "stage": record.stage,
        "paper": {"arxiv_id": record.paper.arxiv_id, "title": record.paper.title, "url": record.paper.url},
        "spent_usd": round(record.budget.spent_usd, 4),
        "gpu_seconds": round(record.budget.gpu_seconds, 1),
        "test_runs_used": record.counters.test_runs_used,
        "flags": flags,
        "error": {"stage": record.error.stage, "reason": record.error.reason} if record.error else None,
    }


def run_daily(
    config: Config, *, llm: str, no_gpu: bool, builder: str, reference_dir: Path | None, today: date, publish: bool,
    run_dir: Path | None = None,
    runs_repo_factory: Callable[..., RunsRepo] = RunsRepo, site_builder=build_site, pipeline=run_pipeline,
    preflight=run_preflight, notifier=notify, stdout=print, env=None,
) -> DailyResult:
    """One day. With `run_dir`, resume that run instead of creating today's (publishing included)."""
    result = DailyResult()
    kwargs = {"env": env} if env is not None else {}
    result.checks = preflight(config, llm=llm, no_gpu=no_gpu, builder=builder, publish=publish, **kwargs)
    stdout(format_checks(result.checks))
    if not all_ok(result.checks):
        stdout("preflight failed; nothing was started")
        result.notified = notifier(config.notify_url, _payload(None, "preflight_failed", format_checks(result.checks)))
        return result

    repo = runs_repo_factory(config.runs_root, config.runs_repo_url if publish else "")
    if publish:
        try:
            repo.ensure()
        except GitError as exc:
            result.publish_error = str(exc)
            stdout(f"runs repository unavailable: {exc}")
            result.notified = notifier(config.notify_url, _payload(None, "repo_unavailable", str(exc)))
            return result

    if run_dir is not None:
        record = RunRecord.load(run_dir)
        stdout(f"resuming {record.run_dir} at stage {record.stage}")
    else:
        record = create_run(config.runs_root, today, _caps(config), config.budget.limit_usd)
        stdout(f"created {record.run_dir}")
    result.run_dir, result.record = record.run_dir, record

    def _publish(paths: list[Path], message: str) -> None:
        if not publish:
            return
        try:
            repo.publish([p for p in paths if p.exists()], message)
            result.published = True
        except GitError as exc:  # the commit stays local; the run goes on; the next publish retries everything
            result.published = False
            result.publish_error = str(exc)
            stdout(f"publish failed ({message}): {exc}")

    def hook(rec: RunRecord, stage: str) -> None:
        _publish([rec.run_dir, config.runs_root / SEEN_FILE], f"run {rec.run_id}: {stage}")

    ctx = RunContext(
        config=config, no_gpu=no_gpu, builder=builder, reference_dir=reference_dir, llm=llm,
        on_stage_done=hook if publish else None,
    )
    status, message = "finished", ""
    try:
        pipeline(record.run_dir, ctx)
    except BaseException as exc:
        status, message = "crashed", f"{type(exc).__name__}: {exc}"
        raise
    finally:
        # Even a crashed stage is visible on the dashboard and in the runs repository.
        try:
            result.site = site_builder(config.runs_root)
        except Exception as exc:  # never mask the stage's own error with a dashboard error
            stdout(f"dashboard build failed: {type(exc).__name__}: {exc}")
        # The final publish stages the whole runs root, so resumed or hand-edited runs reach the remote too.
        _publish([config.runs_root], f"run {record.run_id}: dashboard")
        try:
            result.record = RunRecord.load(record.run_dir)
        except Exception:
            pass
        result.notified = notifier(config.notify_url, _payload(result.record, status, message))
    return result
