"""Report stage: summary.md. Commit, push, dashboard and notifications land in build step 6."""
from __future__ import annotations

from paper2code.manager.graph import RunContext
from paper2code.manager.record import RunRecord, utcnow
from paper2code.manager.verdict import VERDICT_JSON, Verdict


def render_summary(record: RunRecord, verdict: Verdict | None) -> str:
    outcome = record.outcome.value if record.outcome else "none"
    paper_line = f"- **Paper:** {record.paper.title or '(none)'} ({record.paper.arxiv_id or 'no id'}) {record.paper.url}"
    lines = [
        f"# Run {record.run_id}",
        "",
        paper_line.rstrip(),
        f"- **Outcome:** `{outcome}`",
        f"- **Stage reached:** {record.stage}",
        f"- **Test runs used:** {record.counters.test_runs_used} of {record.caps.test_runs}",
        f"- **GPU seconds:** {record.budget.gpu_seconds:.1f}",
        f"- **Spent:** {record.budget.spent_usd:.2f} USD of {record.budget.limit_usd:.2f} USD",
        f"- **Started:** {record.started_at}",
        f"- **Finished:** {record.finished_at}",
        "",
        "## Inspector",
        "",
    ]
    if verdict is None:
        lines.append("no verdict (run ended before inspection)")
    else:
        lines.append(verdict.summary)
        if verdict.confidence is not None:
            lines.append(f"- **Inspector confidence:** {verdict.confidence:.2f}")
        if verdict.flags:
            lines.append("")
            lines.append("Flags:")
            for f in verdict.flags:
                where = f"{f.file}:{f.line}" if f.line is not None else f.file
                lines.append(f"- `{f.kind}` ({f.source}) at {where}: {f.note}")
    if record.error is not None:
        lines += [
            "",
            "## Error",
            "",
            f"Stage `{record.error.stage}`, reason `{record.error.reason}`: {record.error.message}",
        ]
    return "\n".join(lines) + "\n"


def run(record: RunRecord, ctx: RunContext) -> None:
    record.finished_at = utcnow()
    verdict = Verdict.load(record.run_dir) if (record.run_dir / VERDICT_JSON).exists() else None
    (record.run_dir / "summary.md").write_text(render_summary(record, verdict), encoding="utf-8")
